"""Embed a verified onedir Python core in the stable public Windows executable."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from app_version import APP_VERSION


def build(core, output):
    core, output = Path(core).resolve(), Path(output).resolve()
    if not (core/'ChatGPTRemote.Core.exe').is_file():
        raise RuntimeError('Packaged core is missing')
    output.parent.mkdir(parents=True, exist_ok=True)
    archive_path = output.with_suffix('.core.zip')
    entries = []
    with ZipFile(archive_path, 'w', ZIP_DEFLATED) as archive:
        for path in sorted(core.rglob('*')):
            if path.is_symlink(): raise ValueError('Redirected core file')
            if not path.is_file(): continue
            data = path.read_bytes()
            name = path.relative_to(core).as_posix()
            archive.writestr(name, data)
            entries.append({'path':name, 'size':len(data), 'sha256':hashlib.sha256(data).hexdigest()})
        manifest = json.dumps({'files':entries}, separators=(',', ':')).encode('utf-8')
        archive.writestr('CORE-MANIFEST.json', manifest)
    with ZipFile(archive_path) as archive:
        if archive.testzip() is not None: raise RuntimeError('Core ZIP verification failed')
    agent = Path(__file__).resolve().parent
    generated = output.with_suffix('.generated.cs')
    generated.write_text((agent/'remote_launcher.cs').read_text(encoding='utf-8')
                         .replace('__FILE_VERSION__', '.'.join((APP_VERSION.split('.')+['0']*4)[:4]))
                         .replace('__PAYLOAD_VERSION__', APP_VERSION)
                         .replace('__PAYLOAD_SHA256__', hashlib.sha256(archive_path.read_bytes()).hexdigest())
                         .replace('__MANIFEST_SHA256__', hashlib.sha256(manifest).hexdigest()), encoding='utf-8')
    compiler = Path(os.environ.get('WINDIR', r'C:\Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
                    '/out:'+str(output), '/win32icon:'+str(agent/'desktop.ico'),
                    '/reference:System.Windows.Forms.dll', '/reference:System.Web.Extensions.dll',
                    '/reference:System.IO.Compression.dll',
                    '/resource:'+str(archive_path)+',ChatGPTRemote.Core.zip',
                    str(agent/'update_common.cs'), str(generated)], check=True)
    return output


if __name__ == '__main__':
    print(build(*sys.argv[1:]))
