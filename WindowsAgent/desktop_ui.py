"""Assemble local desktop resources into one self-contained document."""
import base64
from pathlib import Path

HERE=Path(__file__).resolve().parent


def render_html(theme='light', native=True):
    html=(HERE/'desktop.html').read_text(encoding='utf-8')
    for marker,name in [('TOKENS','ui-tokens.css'),('MATERIAL','ui-material.css'),
                        ('MOTION','desktop-motion.js'),('PREFERENCES','desktop-preferences.js')]:
        html=html.replace('/*REMOTE_'+marker+'*/',(HERE/'assets'/name).read_text(encoding='utf-8'))
    html=html.replace('<!--RM_MARK-->',(HERE/'assets/rm-mark.svg').read_text(encoding='utf-8'))
    html=html.replace('REMOTE_SUPPORT_QR', 'data:image/png;base64,'+base64.b64encode((HERE/'assets/support-alipay.png').read_bytes()).decode())
    html=html.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-theme="'+theme+'">')
    html=html.replace('/*REMOTE_HOST*/','window.REMOTE_TRAY_HOST='+('true' if native else 'false')+';')
    return html
