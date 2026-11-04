"""Generated icon frames and the native bridge, without opening a window."""
from pathlib import Path
import os
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class AssetTests(unittest.TestCase):
    def test_each_taskbar_icon_size_has_an_opaque_tile_and_readable_letters(self):
        from PIL import Image
        icon=Image.open(Path(__file__).resolve().parents[1]/'desktop.ico')
        for size in (16,20,24,32,40,48,64):
            frame=icon.ico.getimage((size,size)).convert('RGBA')
            pixels=[frame.getpixel((x,y)) for y in range(size) for x in range(size)]
            dark=sum(a>240 and max(r,g,b)<100 for r,g,b,a in pixels)
            white=sum(a>240 and min(r,g,b)>200 for r,g,b,a in pixels)
            self.assertGreater(dark,size*size*.25);self.assertGreater(white,size*size*.07)
            self.assertLess(frame.getpixel((0,0))[3],64)

    @unittest.skipUnless(os.name=='nt','Windows native backdrop')
    def test_native_bridge_loads_and_rejects_an_invalid_window(self):
        from desktop_material import NativeBackdrop
        with self.assertRaises(OSError):
            NativeBackdrop(Path(__file__).resolve().parents[1]/'assets/ChatGPTRemote.Backdrop.dll',0)

if __name__=='__main__':unittest.main()
