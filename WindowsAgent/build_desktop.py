"""Build the desktop assets using the installed MSVC and Windows SDK."""
from pathlib import Path
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def build_backdrop():
    programs=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))
    locator=programs/'Microsoft Visual Studio/Installer/vswhere.exe'
    location=subprocess.check_output([str(locator),'-latest','-products','*','-requires',
        'Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath'],text=True).strip()
    if not location:raise RuntimeError('Building the backdrop requires MSVC C++ Build Tools')
    def latest(folder):
        return max((p for p in folder.iterdir() if p.is_dir() and p.name[0].isdigit()),
                   key=lambda p:tuple(map(int,p.name.split('.'))))
    vc=latest(Path(location)/'VC/Tools/MSVC')
    sdk=programs/'Windows Kits/10';version=latest(sdk/'Include').name
    work=HERE.parent/'_wip/desktop-runtime/build';work.mkdir(parents=True,exist_ok=True)
    output=HERE/'assets/ChatGPTRemote.Backdrop.dll'
    includes=[vc/'include',*(sdk/'Include'/version/p for p in ('ucrt','shared','um','winrt','cppwinrt'))]
    libraries=[vc/'lib/x64',*(sdk/'Lib'/version/p/'x64' for p in ('um','ucrt'))]
    command=[str(vc/'bin/Hostx64/x64/cl.exe'),'/nologo','/LD','/EHsc','/std:c++17','/O1','/MT',
        *('/I'+str(p) for p in includes),'/Fo'+str(work/'native_backdrop.obj'),
        str(HERE/'native_backdrop.cpp'),'/link','/OUT:'+str(output),
        '/IMPLIB:'+str(work/'native_backdrop.lib'),*('/LIBPATH:'+str(p) for p in libraries),
        'WindowsApp.lib','CoreMessaging.lib','user32.lib']
    env={**os.environ,'TEMP':str(work),'TMP':str(work)}
    result=subprocess.run(command,cwd=work,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace')
    (work/'compile.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError('Backdrop compilation failed; see _wip/desktop-runtime/build/compile.log')
    return output


def build_preview():
    sys.path.insert(0, str(HERE.parent / '_wip/desktop-runtime/libs'))
    sys.path.insert(0, str(HERE.parent / 'tools'))
    from build_review import build
    return build(HERE.parent, HERE.parent / 'ChatGPT-Remote-Preview.html')


if __name__ == '__main__':
    print(build_preview() if '--preview' in sys.argv else build_backdrop())
