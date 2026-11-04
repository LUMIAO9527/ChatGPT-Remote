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
    import base64
    import io
    import json
    sys.path.insert(0,str(HERE.parent/'_wip/desktop-runtime/libs'))
    import qrcode
    from desktop_ui import render_html
    from agent import VERSION
    buffer=io.BytesIO()
    qrcode.make('https://computer.example.ts.net:8443/',border=2,box_size=5).save(buffer,format='PNG')
    qr='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()
    demo={'running':True,'version':VERSION,'computerName':'我的电脑',
          'phoneUrl':'https://computer.example.ts.net:8443/',
          'windows':[{'id':1,'title':'ChatGPT · 项目工作区','selected':True}],
          'devices':[{'id':'demo-phone','name':'手机','created':1793836800}],
          'preferences':{'theme':'light','material':'glass','autoHide':True},
          'update':{'status':'idle','version':VERSION,'notes':''}}
    bridge='''window.REMOTE_PREVIEW=true;
const previewData=DEMO_DATA;let previewAuto=true;
window.pywebview={api:{call:async(action,payload={})=>{
 if(action==='status')return {ok:true,...structuredClone(previewData)};
 if(action==='pair')return {ok:true,pin:'123456',qr:DEMO_QR,expiresAt:Date.now()/1000+900};
 if(action==='settings.get')return {ok:true,preferences:{...previewData.preferences},autoStart:previewAuto};
 if(action==='settings.autostart'){previewAuto=payload.enabled;return {ok:true,autoStart:previewAuto};}
 if(action.startsWith('settings.')){const key=action.slice(9);previewData.preferences[key]=payload[key];if(key==='material')document.documentElement.dataset.material=payload[key];return {ok:true,preferences:{...previewData.preferences}};}
 if(action==='device.rename'){previewData.devices.find(d=>d.id===payload.id).name=payload.name;return {ok:true};}
 if(action==='device.remove'){previewData.devices=previewData.devices.filter(d=>d.id!==payload.id);return {ok:true};}
 if(action==='updates.check'){previewData.update={status:'current',version:previewData.version,notes:''};return {ok:true,update:previewData.update};}
 if(action==='links.open'){const paths={github:'',issues:'/issues/new/choose',releases:'/releases/latest'};window.open('https://github.com/LUMIAO9527/ChatGPT-Remote'+paths[payload.page],'_blank','noopener');}
 if(action==='link.copy'||action==='support.copy'){try{await navigator.clipboard.writeText(action==='link.copy'?previewData.phoneUrl:'210037309');}catch{return {ok:false,error:'请手动复制'};}}
 return {ok:true};
}}};document.documentElement.dataset.material='glass';document.documentElement.dataset.materialPreview='dawn';
'''.replace('DEMO_DATA',json.dumps(demo,ensure_ascii=False)).replace('DEMO_QR',json.dumps(qr))
    html=render_html(native=False).replace('<script>','<script>'+bridge,1)
    html=html.replace('<body class="live">','<body class="demo"><span class="preview-caption">界面预览 · 示例数据</span>')
    html=html.replace('</style>','body.demo{display:grid;place-items:center;min-height:100vh;overflow:auto;padding:36px}body.demo #shell{width:312px;height:458px;box-shadow:var(--shadow-window)}.preview-caption{position:fixed;top:12px;left:16px;font:11px Segoe UI,sans-serif;color:#515c6c}</style>')
    output=HERE.parent/'ChatGPT-Remote-Preview.html'
    output.write_text(html,encoding='utf-8')
    return output


if __name__ == '__main__':
    print(build_preview() if '--preview' in sys.argv else build_backdrop())
