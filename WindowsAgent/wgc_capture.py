"""Windows Graphics Capture backend with a bounded latest-frame buffer.

WGC pushes frames when the window content changes, so it costs nothing while
the desktop is idle. The frame hub keeps polling; this class only answers
"what is the newest frame right now" and never blocks the capture thread.
"""
from __future__ import annotations

import threading
import time
from typing import Any


class WgcUnavailable(RuntimeError):
    pass


class WgcCapture:
    """Owns one WGC session for one window title."""

    def __init__(self, window_name: str, target_width: int = 900) -> None:
        self.window_name = window_name
        self.target_width = max(320, min(2560, int(target_width or 900)))
        self._lock = threading.Lock()
        self._image = None
        self._size = None
        self._at = 0.0
        self._error: BaseException | None = None
        self._frames = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        try:
            from windows_capture import WindowsCapture  # noqa: F401
        except Exception as error:  # pragma: no cover - optional dependency
            raise WgcUnavailable(f"windows_capture 不可用：{error}") from error
        capture = WindowsCapture(window_name=self.window_name)
        self._capture = capture

        def on_frame_arrived(frame: Any, control: Any) -> None:
            try:
                image = self._convert(frame)
            except Exception as error:  # keep streaming with the last frame
                self._error = error
                return
            with self._lock:
                self._image = image
                self._size = (frame.width, frame.height)
                self._at = time.monotonic()
                self._frames += 1
            if self._stop.is_set():
                control.stop()

        def on_closed() -> None:
            self._error = WgcUnavailable("窗口已关闭")

        capture.event(on_frame_arrived)
        capture.event(on_closed)
        self._thread = threading.Thread(target=self._run, name="wgc-capture", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._capture.start()  # blocks until stopped
        except BaseException as error:  # pragma: no cover - native failure
            self._error = error

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._capture is not None:
                self._capture.stop()
        except Exception:
            pass
        self._thread = None

    # -- reads -------------------------------------------------------------
    def latest(self, max_age: float = 10.0):
        with self._lock:
            if self._image is None:
                return None
            if time.monotonic() - self._at > max_age:
                return None
            return self._image

    def frame_size(self) -> tuple[int, int] | None:
        with self._lock:
            return self._size

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"frames": self._frames, "age_ms": round((time.monotonic() - self._at) * 1000, 1)
                    if self._at else None, "size": self._size,
                    "error": type(self._error).__name__ if self._error else None}

    # -- conversion --------------------------------------------------------
    def _convert(self, frame: Any):
        import io

        import numpy as np
        from PIL import Image

        # GET_FRAME_BUFFER hands back a strided ndarray; PIL needs a flat one.
        buffer = frame.frame_buffer
        if not buffer.flags.c_contiguous:
            buffer = np.ascontiguousarray(buffer)
        image = Image.frombuffer("RGB", (frame.width, frame.height), buffer, "raw", "BGRX", 0, 1)
        width, height = image.size
        if width > self.target_width:
            image = image.resize((self.target_width, round(height * self.target_width / width)),
                                 Image.BILINEAR)
        return image.copy()