"""Run the installed Remote plugin for the lifetime of a ChatGPT desktop process."""

import asyncio
import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import re
import sys
import tomllib
from contextlib import contextmanager


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "chatgpt-remote@chatgpt-remote-local"
PACKAGE_FAMILY = "OpenAI.Codex_2p2nqsd0c76g0"
WAIT_TIMEOUT = 258


def plugin_enabled(codex_home):
    try:
        with (codex_home / "config.toml").open("rb") as config:
            settings = tomllib.load(config)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return False
    return settings.get("plugins", {}).get(PLUGIN, {}).get("enabled") is True


def installed_package(codex_home):
    folder = codex_home / "plugins/cache/chatgpt-remote-local/chatgpt-remote"
    if not folder.is_dir():
        return None
    versions = [
        package for package in folder.iterdir()
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", package.name)
        and (package / "server.py").is_file() and (package / "runtime-manifest.json").is_file()
    ]
    return max(versions, key=lambda package: tuple(map(int, package.name.split("."))), default=None)


@contextmanager
def chatgpt_process(pid):
    """Hold an identity-checked process handle so PID reuse cannot extend the run."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetPackageFamilyName.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        yield None
        return
    try:
        own_session, app_session = wintypes.DWORD(), wintypes.DWORD()
        same_session = (
            kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(own_session))
            and kernel.ProcessIdToSessionId(pid, ctypes.byref(app_session))
            and own_session.value == app_session.value
        )
        family = ctypes.create_unicode_buffer(128)
        family_length = wintypes.DWORD(len(family))
        image = ctypes.create_unicode_buffer(1024)
        image_length = wintypes.DWORD(len(image))
        identified = (
            same_session
            and kernel.GetPackageFamilyName(handle, ctypes.byref(family_length), family) == 0
            and family.value.casefold() == PACKAGE_FAMILY.casefold()
            and kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(image_length))
            and Path(image.value).name.casefold() == "chatgpt.exe"
        )
        if not identified:
            yield None
            return

        def running():
            return kernel.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT

        yield running
    finally:
        kernel.CloseHandle(handle)


async def run_plugin(codex_home, package, app_running):
    os.environ["CHATGPTREMOTE_PROJECT"] = str(ROOT)
    os.environ["CHATGPTREMOTE_DATA"] = str(ROOT / "_data")
    sys.path.insert(0, str(package))
    from server import lifespan, server

    async with lifespan(server):
        while app_running() and plugin_enabled(codex_home) and package.is_dir():
            await asyncio.sleep(0.5)


def main():
    if os.name != "nt" or len(sys.argv) != 2 or not sys.argv[1].isascii() or not sys.argv[1].isdecimal():
        return 0
    pid = int(sys.argv[1])
    if not 0 < pid <= 0xFFFFFFFF:
        return 0
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    with chatgpt_process(pid) as app_running:
        if app_running is None or not app_running() or not plugin_enabled(codex_home):
            return 0
        package = installed_package(codex_home)
        if package is None:
            return 0
        try:
            asyncio.run(run_plugin(codex_home, package, app_running))
        except Exception as error:
            logging.error("Remote app host failed: %s", type(error).__name__)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
