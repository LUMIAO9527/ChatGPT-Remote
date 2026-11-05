"""Contrast and native handle lifecycle for theme-aware application marks."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
HERE = Path(__file__).resolve().parents[1]


class ThemeAssetTests(unittest.TestCase):
    def test_both_palettes_keep_the_same_letter_geometry_and_readable_frames(self):
        from PIL import Image
        paths = []
        for theme, background in (('light', 255), ('dark', 23)):
            svg = ET.fromstring((HERE / ('assets/rm-mark-' + theme + '.svg')).read_bytes())
            paths.append([node.attrib['d'] for node in svg.iter() if node.tag.endswith('path')])
            with Image.open(HERE / ('desktop-' + theme + '.ico')) as icon:
                self.assertEqual(icon.ico.sizes(), {(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)})
                for size in icon.ico.sizes():
                    frame = icon.ico.getimage(size).convert('RGBA')
                    pixel = frame.getpixel((size[0] // 2, round(size[1] * .88)))
                    self.assertLess(max(abs(value - background) for value in pixel[:3]), 15)
                    self.assertGreater(pixel[3], 240)
                    pixels = list(frame.getdata())
                    ink = sum(alpha > 240 and (max((red, green, blue)) < 80 if theme == 'light' else min((red, green, blue)) > 200)
                              for red, green, blue, alpha in pixels)
                    self.assertGreater(ink, size[0] * size[1] * .07)
        self.assertEqual(paths[0], paths[1])

    def test_public_svg_resolves_an_explicit_palette_and_has_an_automatic_variant(self):
        from agent import website_icon
        self.assertIn(b'stroke="#171717"', website_icon('svg', 'light'))
        self.assertIn(b'stroke="#ffffff"', website_icon('svg', 'dark'))
        self.assertIn(b'prefers-color-scheme:dark', website_icon('svg'))
        self.assertEqual(website_icon('svg', '../private'), website_icon('svg'))


class NativeThemeTests(unittest.TestCase):
    def icon(self):
        from desktop_tray import TrayIcon
        icon = TrayIcon.__new__(TrayIcon)
        icon._icon_path = {'light': 'light.ico', 'dark': 'dark.ico'}
        icon._loaded_icon_path = 'dark.ico'
        icon._icon_size = 16
        icon._hicon, icon._hwnd = 111, 9
        icon._taskbar_created = 42
        icon._user32 = Mock()
        icon._user32.GetDpiForSystem.return_value = 96
        icon._register_icon = Mock(return_value=True)
        return icon

    def test_system_theme_message_swaps_the_icon_and_releases_the_previous_handle(self):
        from desktop_tray import WM_SETTINGCHANGE
        icon = self.icon()
        with patch('desktop_tray.taskbar_theme', return_value='light'), patch('desktop_tray._load_hicon', return_value=222) as load:
            self.assertEqual(icon._wndproc(9, WM_SETTINGCHANGE, 0, 0), 0)
            load.assert_called_once_with('light.ico', 16)
        self.assertEqual(icon._hicon, 222)
        icon._register_icon.assert_called_once_with('theme')
        icon._user32.DestroyIcon.assert_called_once_with(111)

    def test_failed_replacement_keeps_the_existing_notification_icon(self):
        icon = self.icon()
        with patch('desktop_tray.taskbar_theme', return_value='light'), patch('desktop_tray._load_hicon', return_value=0):
            self.assertFalse(icon._refresh_icon('theme'))
        self.assertEqual(icon._hicon, 111)
        icon._register_icon.assert_not_called()
        icon._user32.DestroyIcon.assert_not_called()

    def test_taskbar_appearance_uses_the_system_setting(self):
        from desktop_tray import taskbar_theme
        with patch('winreg.OpenKey') as key, patch('winreg.QueryValueEx', return_value=(0, 4)) as query:
            self.assertEqual(taskbar_theme(), 'dark')
            query.assert_called_once_with(key.return_value.__enter__.return_value, 'SystemUsesLightTheme')


if __name__ == '__main__':
    unittest.main()
