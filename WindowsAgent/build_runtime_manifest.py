"""Seal the bundled managed runtime files into the program's resource archive."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

def build(destination, *components):
    files={}
    for name in ('pythonnet','clr_loader','webview'):
        spec=importlib.util.find_spec(name)
        if spec is None or spec.origin is None:raise RuntimeError('Missing build dependency: '+name)
        base=Path(spec.origin).parent
        for source in sorted(base.rglob('*.dll')):
            files[name+'/'+source.relative_to(base).as_posix()]=hashlib.sha256(source.read_bytes()).hexdigest()
    config=Path(__file__).with_name('netfx.config')
    files['netfx.config']=hashlib.sha256(config.read_bytes()).hexdigest()
    for component in map(Path, components):
        files[component.name]=hashlib.sha256(component.read_bytes()).hexdigest()
    Path(destination).write_text(json.dumps({'schema':1,'files':files},indent=2),encoding='utf8')
    from app_version import APP_VERSION
    from PyInstaller.utils.win32.versioninfo import VSVersionInfo,FixedFileInfo,StringFileInfo,StringTable,StringStruct,VarFileInfo,VarStruct
    numbers=tuple(map(int,APP_VERSION.split('.')))+(0,)
    values={'CompanyName':'LUMIAO9527','FileDescription':'ChatGPT Remote','FileVersion':APP_VERSION+'.0',
            'InternalName':'ChatGPTRemote','OriginalFilename':'ChatGPTRemote.exe','ProductName':'ChatGPT Remote','ProductVersion':APP_VERSION}
    info=VSVersionInfo(ffi=FixedFileInfo(filevers=numbers,prodvers=numbers),kids=[
        StringFileInfo([StringTable('080404b0',[StringStruct(key,value) for key,value in values.items()])]),
        VarFileInfo([VarStruct('Translation',[0x0804,1200])])])
    Path(destination).with_name('version-info.txt').write_text(str(info),encoding='utf8')

if __name__=='__main__':build(*sys.argv[1:])
