"""Local MCP entrypoint for ChatGPT Remote. stdout belongs to MCP only."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(os.environ['CHATGPTREMOTE_PROJECT']).resolve()
sys.dont_write_bytecode = True
os.environ['CHATGPTREMOTE_HOME'] = str(ROOT)
os.environ.setdefault('CHATGPTREMOTE_DATA', str(ROOT / '_data'))
PACKAGE = Path(__file__).resolve().parent


def runtime_directory():
    # The desktop installer can retire a cache version while its worker still
    # runs. Load dependencies from a stable copy on the project drive.
    if PACKAGE == ROOT / 'plugin' or os.environ.get('CHATGPTREMOTE_MOCK') == '1':
        return PACKAGE
    manifest = PACKAGE / 'runtime-manifest.json'
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()[:20]
    parent = ROOT / '_wip' / 'runtime' / 'plugin'
    destination = parent / digest
    if not destination.is_dir():
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='stage-', dir=parent) as folder:
            staged = Path(folder) / 'runtime'
            shutil.copytree(PACKAGE / 'core', staged / 'core')
            shutil.copytree(PACKAGE / 'vendor', staged / 'vendor')
            shutil.copy2(manifest, staged / manifest.name)
            try:
                staged.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
    return destination


RUNTIME = runtime_directory()
os.chdir(ROOT)
sys.path[:0] = [str(RUNTIME / 'vendor'), str(RUNTIME / 'core' if (RUNTIME / 'core').is_dir() else ROOT / 'WindowsAgent')]

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp.types as types
from app_version import APP_VERSION
from desktop_control import DesktopControlError
from plugin_popover import NativePopover
from remote_core import RemoteController

ICON = types.Icon(src='data:image/svg+xml;base64,' + base64.b64encode(
    (Path(__file__).parent/'assets/icon.svg').read_bytes()).decode(), mimeType='image/svg+xml', sizes=['32x32'])
controller = RemoteController(ROOT, Path(os.environ['CHATGPTREMOTE_DATA']) if os.environ.get('CHATGPTREMOTE_DATA') else None)


@asynccontextmanager
async def lifespan(_):
    controller.runtime.begin()
    popover = None
    if os.name == 'nt' and os.environ.get('CHATGPTREMOTE_MOCK') != '1':
        popover = NativePopover(controller, RUNTIME / 'core' / 'ChatGPTRemote.Popover.dll')
        popover.thread.start()
    try:
        yield {}
    finally:
        if popover is not None:
            await asyncio.to_thread(popover.close)
        await asyncio.to_thread(controller.runtime.close)


server = Server('chatgpt-remote', version=APP_VERSION, lifespan=lifespan,
                instructions='The Remote button above Usage in the ChatGPT desktop window opens pairing and device settings.',
                icons=[ICON])


def tool(name, description, schema, *, readonly=False):
    return types.Tool(name=name, description=description, inputSchema=schema, icons=[ICON],
                      annotations=types.ToolAnnotations(readOnlyHint=readonly, destructiveHint=False,
                                                        idempotentHint=readonly, openWorldHint=False))


@server.list_tools()
async def list_tools():
    return [
        tool('remote_status', 'Read the phone connection and paired device status.',
             {'type': 'object', 'properties': {}, 'additionalProperties': False}, readonly=True),
        tool('remote_service', 'Start, stop or restart this computer’s phone connection when requested.',
             {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['start', 'stop', 'restart']}},
              'required': ['action'], 'additionalProperties': False}),
    ]


def public_status(value):
    return {key: value[key] for key in ('running', 'version', 'pluginVersion', 'computerName',
        'targetStatus', 'paused', 'devices', 'managedBy', 'runtimeError', 'localPort') if key in value}


@server.call_tool()
async def call_tool(name, arguments):
    try:
        if name == 'remote_status':
            value = await asyncio.to_thread(controller.status)
        elif name == 'remote_service':
            value = await asyncio.to_thread(controller.service, arguments['action'])
        else:
            raise DesktopControlError('没有这个 Remote 工具')
        public = public_status(value)
        return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(public, ensure_ascii=False))],
            structuredContent=public)
    except (DesktopControlError, OSError, ValueError) as error:
        return types.CallToolResult(content=[types.TextContent(type='text', text=str(error))], isError=True)


async def main():
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == '__main__':
    asyncio.run(main())
