"""Generate the local plugin manifests from the project's single version source."""
from pathlib import Path
import argparse
import ast
import hashlib
import importlib.metadata as metadata
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'WindowsAgent'))
from app_version import APP_VERSION


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def compile_popover(agent, core):
    framework = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319'
    references = ['System.Drawing.dll', 'System.Windows.Forms.dll', 'System.Web.Extensions.dll']
    references += [str(framework / 'WPF' / name) for name in
                   ('UIAutomationClient.dll', 'UIAutomationTypes.dll', 'WindowsBase.dll')]
    webview = metadata.distribution('pywebview')
    library = Path(webview.locate_file('webview/lib'))
    for name in ('Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll'):
        shutil.copy2(library / name, core / name)
        references.append(str(core / name))
    shutil.copy2(library / 'runtimes/win-x64/native/WebView2Loader.dll', core / 'WebView2Loader.dll')
    subprocess.run(['node', str(ROOT / 'tools/build_panel.cjs'), '--native', str(core / 'popover/index.html')],
                   cwd=ROOT, check=True)
    sources = [agent / 'sidebar_button.cs', agent / 'plugin_popover.cs']
    sources += sorted((agent / 'popover').glob('*.cs'))
    subprocess.run([str(framework / 'csc.exe'), '/nologo', '/target:library', '/platform:x64',
                    '/optimize+', '/codepage:65001', '/utf8output', f'/out:{core / "ChatGPTRemote.Popover.dll"}',
                    *[f'/reference:{ref}' for ref in references],
                    *[str(path) for path in sources]], check=True)


def bundle_runtime(plugin):
    from packaging.requirements import Requirement
    agent = ROOT / 'WindowsAgent'
    core = plugin / 'core'
    core.mkdir(exist_ok=True)
    queue = ['remote_core', 'plugin_runtime', 'plugin_popover']
    copied = set()
    while queue:
        name = queue.pop()
        source = agent / (name + '.py')
        if name in copied or not source.is_file():
            continue
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                queue.extend(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                queue.append(node.module.split('.')[0])
        shutil.copy2(source, core / source.name)
        copied.add(name)
    for name in ('uia_reader.ps1', 'desktop.ico', 'netfx.config'):
        shutil.copy2(agent / name, core / name)
    compile_popover(agent, core)
    shutil.copytree(agent / 'assets', core / 'assets', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(agent / 'web', core / 'web', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    vendor = plugin / 'vendor'
    vendor.mkdir(exist_ok=True)
    packages, seen = {'pywebview (WebView2 interop)': metadata.version('pywebview')}, set()
    queue = [Requirement(name) for name in ('mcp>=1.28,<2', 'Pillow>=10,<13', 'qrcode>=8,<9',
                                             'markdown-it-py>=4,<5', 'pythonnet>=3.0,<4')]
    while queue:
        request = queue.pop()
        key = (request.name.lower(), frozenset(request.extras))
        if key in seen:
            continue
        seen.add(key)
        distribution = metadata.distribution(request.name)
        if distribution.version not in request.specifier:
            raise RuntimeError('Plugin dependency version does not match: ' + request.name)
        if distribution.metadata['Name'] not in packages:
            base = Path(distribution.locate_file('')).resolve()
            for entry in distribution.files or ():
                source = Path(distribution.locate_file(entry)).resolve()
                if not source.is_relative_to(base) or source.suffix == '.pyc' or '__pycache__' in source.parts:
                    continue
                if not source.is_file():
                    continue
                target = vendor / source.relative_to(base)
                target.parent.mkdir(parents=True, exist_ok=True)
                if source != target.resolve():
                    shutil.copy2(source, target)
            packages[distribution.metadata['Name']] = distribution.version
        for dependency in distribution.requires or ():
            dep = Requirement(dependency)
            if dep.marker is None or any(dep.marker.evaluate({'extra': extra}) for extra in ('', *request.extras)):
                queue.append(dep)
    inventory = {}
    for folder in (core, vendor):
        for path in folder.rglob('*'):
            if path.is_file() and path.suffix != '.pyc' and '__pycache__' not in path.parts:
                inventory[path.relative_to(plugin).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    write(plugin / 'runtime-manifest.json', {'schema': 1, 'version': APP_VERSION, 'coreModules': sorted(copied),
                                            'packages': packages, 'files': inventory})


def build(plugin):
    source = ROOT / 'plugin'
    sys.path.insert(0, str(ROOT / '_wip/build/libs'))
    if plugin != source:
        plugin.mkdir(parents=True, exist_ok=True)
        for name in ('server.py', 'requirements.txt'):
            shutil.copy2(source / name, plugin / name)
        shutil.copytree(source / 'skills', plugin / 'skills', dirs_exist_ok=True)
    bundle_runtime(plugin)
    (plugin/'assets').mkdir(exist_ok=True)
    for original, name in (('rm-mark-light.svg', 'icon.svg'), ('rm-mark-dark.svg', 'icon-dark.svg')):
        shutil.copy2(ROOT/'WindowsAgent/assets'/original, plugin/'assets'/name)
    interface = {'displayName': 'ChatGPT Remote', 'shortDescription': '手机连接这台电脑',
        'longDescription': '从 ChatGPT 左下角打开手机连接浮层，扫码配对和管理设备。',
        'developerName': 'LUMIAO9527', 'category': 'Productivity',
        'capabilities': ['Interactive', 'Read', 'Write'],
        'logo': './assets/icon.svg', 'composerIcon': './assets/icon.svg',
        'logoDark': './assets/icon-dark.svg', 'composerIconDark': './assets/icon-dark.svg'}
    common = {'name': 'chatgpt-remote', 'version': APP_VERSION,
              'description': 'Connect a phone browser to ChatGPT on this computer.'}
    write(plugin / 'plugin.json', {'$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json',
        **common, 'extensions': {'com.openai': {'interface': interface, 'requires_local_executor': True}}})
    write(plugin / '.codex-plugin/plugin.json', {**common, 'skills': './skills/',
        'mcpServers': './mcp.json', 'requires_local_executor': True, 'interface': interface})
    write(plugin / 'mcp.json', {'$schema': 'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json',
        'mcpServers': {'remote': {'type': 'stdio', 'command': 'python',
            'args': ['-B', './server.py'], 'cwd': './',
            'env': {'CHATGPTREMOTE_PROJECT': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'}}}})
    if plugin == source:
        write(ROOT / '.agents/plugins/marketplace.json', {'name': 'chatgpt-remote-local',
            'interface': {'displayName': 'ChatGPT Remote 本机'},
            'plugins': [{'name': 'chatgpt-remote', 'source': {'source': 'local', 'path': './plugin'},
                         'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}}]})
    print('Plugin manifests generated for '+APP_VERSION)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'plugin')
    build(parser.parse_args().output.resolve())
