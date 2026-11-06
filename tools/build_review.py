"""Bundle the actual product templates with isolated offline API fixtures.

Run from any directory. Output is an interactive review, not an application
release or a replacement for Windows/Mate80 network acceptance.
"""
import argparse
import base64
import io
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def b64(data):
    return base64.b64encode(data).decode()


def build(source: Path, output: Path):
    agent = source / 'WindowsAgent'
    sys.path.insert(0, str(agent))
    from agent import MockBackend
    from app_version import APP_VERSION
    from desktop_icon import vector
    import qrcode
    # Generate a real demonstration QR matrix, never a real connection secret.
    buffer = io.BytesIO()
    qrcode.make('https://demo.invalid/rm/',border=4,box_size=6).save(buffer,format='PNG')
    backend=MockBackend();backend.select(1)
    frame=backend.capture()
    desktop=(agent/'desktop.html').read_text(encoding='utf-8')
    for marker,name in [('TOKENS','ui-tokens.css'),('MATERIAL','ui-material.css'),('MOTION','desktop-motion.js'),('PREFERENCES','desktop-preferences.js'),('LIFECYCLE','desktop-lifecycle.js')]:
        desktop=desktop.replace('/*REMOTE_'+marker+'*/',(agent/'assets'/name).read_text(encoding='utf-8'))
    desktop=desktop.replace('<!--RM_MARK-->',vector())
    desktop=desktop.replace('REMOTE_SUPPORT_QR','data:image/png;base64,'+b64(buffer.getvalue()))
    desktop=desktop.replace('/*REMOTE_HOST*/','window.REMOTE_TRAY_HOST=false;')
    mobile=(agent/'web/index.html').read_text(encoding='utf-8')
    mark='data:image/svg+xml;base64,'+b64(vector('auto').encode('utf-8'))
    cover='data:image/svg+xml;base64,'+b64((agent/'assets/computer-cover.svg').read_bytes())
    for tag in ['mobile','desktop']:
        content=locals()[tag]
        content=re.sub(r'<meta http-equiv="Content-Security-Policy"[^>]*>','',content,flags=re.I)
        content=re.sub(r'/icon\.(?:svg|png)(?:\?[^"\s]*)?',mark,content)
        content=re.sub(r'/computer-cover\.svg(?:\?[^"\s]*)?',cover,content)
        if tag=='mobile':mobile=content
        else:desktop=content
    payload={'mobile':mobile,'desktop':desktop,'fixtures':(ROOT/'tools/review-fixtures.js').read_text(encoding='utf-8'),
             'qr':'data:image/png;base64,'+b64(buffer.getvalue()),'frame':b64(frame),'version':APP_VERSION}
    shell=(ROOT/'tools/review-shell.html').read_text(encoding='utf-8')
    shell=shell.replace('/*REVIEW_PAYLOAD*/', 'const payload='+json.dumps(payload,ensure_ascii=False).replace('</','<\\/')+';')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(shell,encoding='utf-8',newline='\n')
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,default=ROOT/'ChatGPT-Remote-Preview.html')
    args=parser.parse_args()
    print(build(args.source.resolve(),args.output.resolve()))
