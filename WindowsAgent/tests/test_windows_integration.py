"""Optional real-Windows integration test (no ChatGPT account interaction).

Run only when explicitly enabled, because it opens a temporary Tk window and
injects real keyboard input into it:

    set RUN_WINDOWS_E2E=1
    py -3 -m unittest tests.test_windows_integration -v

It verifies the parts the mock backend cannot: PrintWindow capture of another
process, foreground focus, Unicode SendInput, freeze rejection, and automatic
rebinding after the target process restarts. ChatGPT itself is never touched.
"""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from windows_control import TargetError, WindowsBackend

RUN_E2E = os.name == "nt" and os.environ.get("RUN_WINDOWS_E2E") == "1"
HERE = Path(__file__).resolve().parent


@unittest.skipUnless(RUN_E2E, "set RUN_WINDOWS_E2E=1 to run real Windows capture/input tests")
class WindowsIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.title = f"Harmony E2E {os.getpid()}-{int(time.time()) % 100000}"
        self.log = self.dir / "keys.txt"
        self.log.write_text("", encoding="utf-8")
        self.processes: list[subprocess.Popen] = []
        self.backend = WindowsBackend([self.title])
        self.start_app()

    def tearDown(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self.tmp.cleanup()

    # -- helpers -----------------------------------------------------------
    def start_app(self) -> subprocess.Popen:
        process = subprocess.Popen([
            sys.executable, str(HERE / "e2e_window.py"),
            "--title", self.title, "--log", str(self.log),
        ])
        self.processes.append(process)
        return process

    def wait_for_window(self, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for window in self.backend._enumerate():
                if window["title"] == self.title:
                    return window
            time.sleep(0.25)
        self.fail(f"测试窗口未出现：{self.backend._enumerate()!r}")

    @staticmethod
    def frame_image(backend: WindowsBackend):
        from PIL import Image
        meta = backend.capture_meta(max_width=900, quality=70)
        return Image.open(io.BytesIO(meta["jpeg"])).convert("RGB"), meta

    @staticmethod
    def frame_diff_ratio(before, after) -> float:
        from PIL import ImageChops
        small_before = before.resize((160, 100))
        small_after = after.resize((160, 100))
        diff = ImageChops.difference(small_before, small_after).convert("L")
        histogram = diff.histogram()
        changed = sum(histogram[19:])
        return changed / max(1, sum(histogram))

    # -- tests -------------------------------------------------------------
    def test_capture_focus_unicode_input_and_freeze(self):
        window = self.wait_for_window()
        self.backend.select(window["id"])
        info = self.backend.info()
        self.assertEqual(info["status"], "ready")
        before, meta = self.frame_image(self.backend)
        self.assertEqual(meta["method"], "printwindow")
        self.assertGreater(meta["width"], 200)
        self.assertGreater(meta["height"], 120)
        self.assertIsNotNone(before.convert("L").getbbox(), "抓取到的画面全黑")

        text = "Harmony-你好-123"
        epoch = self.backend.info()["epoch"]
        self.backend.action({"kind": "text", "text": text, "epoch": epoch})
        deadline = time.time() + 6
        received = ""
        while time.time() < deadline:
            received = self.log.read_text(encoding="utf-8")
            if text in received:
                break
            time.sleep(0.1)
        self.assertIn(text, received, f"Unicode 输入未完整到达测试窗口：{received!r}")
        self.assertEqual(received, text, "测试窗口内容与发送文本不一致")

        after, _ = self.frame_image(self.backend)
        self.assertGreater(self.frame_diff_ratio(before, after), 0.002,
                           "输入后画面没有可检测的变化")

        # Allowed extra gestures must not raise.
        epoch = self.backend.info()["epoch"]
        self.backend.action({"kind": "tap", "x": 0.9, "y": 0.9, "epoch": epoch})
        self.backend.action({"kind": "scroll", "delta": 120, "epoch": epoch})
        self.backend.action({"kind": "key", "key": "CTRL+A", "epoch": epoch})

        self.backend.freeze(0.0, "e2e freeze")
        with self.assertRaises(TargetError):
            self.backend.action({"kind": "text", "text": "blocked",
                                 "epoch": self.backend.info()["epoch"]})
        self.backend.unfreeze()
        self.backend.action({"kind": "text", "text": "after-freeze",
                             "epoch": self.backend.info()["epoch"]})

    def test_restart_rebinds_without_pairing_again(self):
        window = self.wait_for_window()
        self.backend.select(window["id"])
        first = self.backend.info()
        self.assertEqual(first["status"], "ready")
        first_epoch = first["epoch"]
        self.assertGreater(first_epoch, 0)

        process = self.processes[0]
        process.kill()
        process.wait(timeout=5)
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                self.backend.capture_meta(max_width=640)
            except TargetError:
                break
            time.sleep(0.25)
        else:
            self.fail("目标进程结束后抓取仍未报错")

        self.start_app()
        rebound = None
        deadline = time.time() + 25
        while time.time() < deadline:
            info = self.backend.info()
            if info["status"] in ("rebound", "ready") and info["windows"] and \
                    info["windows"][0]["selected"]:
                rebound = info
                break
            time.sleep(0.3)
        self.assertIsNotNone(rebound, f"重启后未自动重绑：{self.backend.info()!r}")
        self.assertGreater(rebound["epoch"], first_epoch)
        image, meta = self.frame_image(self.backend)
        self.assertEqual(meta["method"], "printwindow")
        self.assertIsNotNone(image.convert("L").getbbox())


if __name__ == "__main__":
    unittest.main()