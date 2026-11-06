"""Self-contained native HTML rendering and initial theme validation."""
from pathlib import Path
import re
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_ui import render_html

class DesktopDocumentTests(unittest.TestCase):
    def test_native_document_embeds_all_local_resources(self):
        document=render_html('dark')
        self.assertIn('data-theme="dark"',document)
        self.assertIn('window.REMOTE_TRAY_HOST=true;',document)
        self.assertNotIn('/*REMOTE_',document)
        self.assertNotIn('<!--RM_MARK-->',document)
        self.assertNotIn('REMOTE_SUPPORT_QR',document)
        self.assertIn('data:image/png;base64,',document)
        self.assertNotRegex(document,r'<script[^>]+src=')
        self.assertNotRegex(document,r'<link[^>]+href=')
        self.assertIn("connect-src 'none'",document)

    def test_review_document_keeps_the_browser_fallback(self):
        self.assertIn('window.REMOTE_TRAY_HOST=false;',render_html(native=False))

    def test_initial_theme_cannot_escape_the_html_attribute(self):
        document=render_html('dark" onload="alert(1)')
        self.assertIn('<html lang="zh-CN" data-theme="light">',document)
        self.assertNotIn('onload="alert',document)

if __name__=='__main__':unittest.main()
