import threading
import time
import unittest
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from frame_clock import FrameClock


class FrameClockTests(unittest.TestCase):
    def test_wait_can_be_interrupted_and_reused(self):
        clock=FrameClock()
        try:
            threading.Timer(.02,clock.set).start()
            start=time.monotonic()
            self.assertTrue(clock.wait(2))
            self.assertLess(time.monotonic()-start,1)
            clock.clear()
            self.assertFalse(clock.wait(.01))
        finally:clock.close()
        self.assertFalse(clock.precise)

    def test_unsupported_platform_keeps_interruptible_fallback(self):
        with patch('frame_clock.os.name','posix'):clock=FrameClock()
        self.assertFalse(clock.precise)
        clock.set();self.assertTrue(clock.wait(0))
        clock.clear();self.assertFalse(clock.wait(.001));clock.close()


if __name__=='__main__':unittest.main()
