"""Windows window discovery, capture, and input for ChatGPT Remote v0.3.

Capture uses PrintWindow(PW_RENDERFULLCONTENT) for a single window, so an
occluded ChatGPT window is still readable; BitBlt screen copy is only the
fallback for window mode and the implementation for full-desktop mode.
No account credentials or conversation text are stored by this module.

This module is imported by the mock backend too, so it must stay importable
without touching Windows APIs at import time.
"""
from __future__ import annotations

import ctypes
import base64
import io
import math
import ntpath
import os
import sys
import threading
import time
from ctypes import wintypes
from typing import Any


class Win32Error(RuntimeError):
    """A Win32 call failed unexpectedly."""


class TargetError(ValueError):
    """Target window is gone/ambiguous/frozen; mapped to HTTP 409."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


class InputError(ValueError):
    """Request parameters are invalid; mapped to HTTP 400."""


# --- Win32 constants -------------------------------------------------------
PW_RENDERFULLCONTENT = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9
VK_MENU = 0x12
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
GA_ROOT = 2
WM_MOUSEMOVE = 0x0200
WINDOW_BUTTONS = {'left': (0x0201, 0x0202, 1), 'right': (0x0204, 0x0205, 2)}
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# Shell / terminal windows must never be mistaken for the target app even when
# their title contains a window keyword (e.g. a console sitting in the project
# directory named ChatGPTHarmonyRemote).
_SHELL_EXES = {
    "conhost.exe", "cmd.exe", "windowsterminal.exe", "wt.exe",
    "powershell.exe", "pwsh.exe", "mintty.exe", "bash.exe", "wsl.exe",
    "openconsole.exe",
}

# Default targets: the ChatGPT/Codex desktop clients, or ChatGPT opened in a
# mainstream browser. Matching merely on a "ChatGPT" substring would otherwise
# pick up this project's own console, ChatGPTnx windows, or Codex computer-use
# overlays.
_APP_EXES = {"chatgpt.exe", "codex.exe"}
_BROWSER_EXES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
                 "vivaldi.exe", "opera.exe", "arc.exe", "chromium.exe"}

# The phone console itself is frequently open in a desktop browser tab; it must
# never become a remote-control target.
_CONSOLE_TITLE_MARKERS = ("chatgpt remote", "chatgpt harmony remote", "手机控制台")


def window_matches(title: str, exe_path: str, *, custom_keywords: bool,
                   keywords: list[str]) -> bool:
    """Return True when a visible window may be offered as a control target."""
    exe = ntpath.basename(exe_path).casefold()
    if exe in _SHELL_EXES:
        return False
    folded = title.casefold()
    if any(marker in folded for marker in _CONSOLE_TITLE_MARKERS):
        return False
    title_hit = any(keyword in folded for keyword in keywords)
    if custom_keywords:
        # Explicit --window-keyword: the user takes responsibility for identity
        # (also used by the optional Windows integration test).
        return title_hit
    if exe in _APP_EXES:
        return True
    return exe in _BROWSER_EXES and title_hit

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class WindowTracker:
    """Stable *application fingerprint* rather than a saved HWND.

    Never rebinds automatically when multiple windows match, because sending a
    message to the wrong ChatGPT account is worse than asking the user to pick
    again. The epoch starts at wall-clock microseconds so commands queued against a
    previous Agent run are rejected even after a restart.
    """

    def __init__(self) -> None:
        self.current_id = 0
        self.fingerprint: dict[str, Any] | None = None
        # Below JavaScript's safe integer limit and increasing across restarts.
        self.epoch = time.time_ns() // 1000
        self.status = "not_selected"

    # -- persistence -------------------------------------------------------
    def load(self, fingerprint: dict[str, Any] | None) -> None:
        if not isinstance(fingerprint, dict):
            return
        path = str(fingerprint.get("path") or "").casefold()
        basename = ntpath.basename(path)
        win_class = str(fingerprint.get("class") or "").casefold()
        if not path and not win_class:
            return
        self.fingerprint = {"path": path, "basename": basename, "class": win_class, "pid": 0}
        self.current_id = 0
        self.status = "waiting"

    def saved_fingerprint(self) -> dict[str, Any] | None:
        if not self.fingerprint:
            return None
        return {"path": self.fingerprint["path"],
                "class": self.fingerprint["class"]}

    # -- selection ---------------------------------------------------------
    def choose(self, window: dict[str, Any]) -> None:
        exe = str(window.get("exe_path", "")).casefold()
        win_class = str(window.get("class", "")).casefold()
        self.fingerprint = {"path": exe, "basename": ntpath.basename(exe),
                            "class": win_class, "pid": int(window.get("pid", 0))}
        self.current_id = int(window["id"])
        self.epoch += 1
        self.status = "ready"

    def resolve(self, windows: list[dict[str, Any]]) -> int:
        if self.fingerprint is None:
            self.status = "not_selected"
            return 0
        fp = self.fingerprint
        path = fp["path"]
        win_class = fp["class"]
        pid = int(fp.get("pid") or 0)

        def class_ok(w: dict[str, Any]) -> bool:
            return str(w.get("class", "")).casefold() == win_class

        def path_of(w: dict[str, Any]) -> str:
            return str(w.get("exe_path", "")).casefold()

        def exact(w: dict[str, Any]) -> bool:
            return bool(path) and class_ok(w) and path_of(w) == path

        def basename_ok(w: dict[str, Any]) -> bool:
            return class_ok(w) and bool(fp["basename"]) and ntpath.basename(path_of(w)) == fp["basename"]

        # The previously bound handle is only reused when the process identity
        # also matches: HWNDs are recycled by Windows.
        if self.current_id:
            same = next((w for w in windows
                         if int(w["id"]) == self.current_id and class_ok(w)
                         and ((not path) or (path_of(w) == path and int(w.get("pid", 0)) == pid))), None)
            if same is not None:
                self.status = "ready"
                return self.current_id

        if not path:
            # Without a process path automatic rebinding is not safe.
            if self.current_id:
                self.current_id = 0
                self.epoch += 1
            self.status = "waiting"
            return 0

        exacts = [w for w in windows if exact(w)]
        candidates = exacts if exacts else [w for w in windows if basename_ok(w)]
        if self.current_id:
            self.current_id = 0
            self.epoch += 1
        if len(candidates) == 1:
            winner = candidates[0]
            self.current_id = int(winner["id"])
            # Adopt the new process identity. Otherwise a later resolve() would
            # compare the stale pid, treat the same window as a fresh rebind and
            # keep bumping the epoch on every frame.
            self.fingerprint = {
                "path": path_of(winner),
                "basename": ntpath.basename(path_of(winner)),
                "class": str(winner.get("class", "")).casefold(),
                "pid": int(winner.get("pid", 0)),
            }
            self.epoch += 1
            self.status = "rebound"
            return self.current_id
        self.status = "ambiguous" if len(candidates) > 1 else "waiting"
        return 0


class WindowsBackend:
    """Real Windows capture/input backend. One instance per Agent process."""

    def __init__(self, title_keywords: list[str] | None = None) -> None:
        if sys.platform != "win32":
            raise RuntimeError("真实窗口控制只能在 Windows 上运行")
        try:
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("缺少依赖：请运行 pip install -r requirements.txt") from exc
        self.custom_keywords = title_keywords is not None
        self.keywords = [x.casefold() for x in (title_keywords or ["ChatGPT", "Codex"])]
        self.u32 = ctypes.windll.user32
        self.k32 = ctypes.windll.kernel32
        self.g32 = ctypes.windll.gdi32
        try:
            self.dwm = ctypes.windll.dwmapi
        except (AttributeError, OSError):
            self.dwm = None
        self.tracker = WindowTracker()
        self.mode = "window"
        self.lock = threading.RLock()
        self.frozen_until = 0.0
        self.frozen_reason = ""
        self.last_capture_ms = 0
        self.last_capture_method = ""
        self._wgc = None
        self._wgc_title = ""
        self._wgc_failed = False
        self._wgc_retry_at = 0.0
        self.conversation_reader = None
        self._set_prototypes()
        self._set_dpi_aware()

    # -- ctypes setup ------------------------------------------------------
    def _set_dpi_aware(self) -> None:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                self.u32.SetProcessDPIAware()
            except AttributeError:
                pass

    def _set_prototypes(self) -> None:
        u32, k32, g32 = self.u32, self.k32, self.g32
        u32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
                                    wintypes.LPARAM]
        u32.EnumWindows.restype = wintypes.BOOL
        u32.IsWindowVisible.argtypes = [wintypes.HWND]
        u32.IsWindowVisible.restype = wintypes.BOOL
        u32.IsWindow.argtypes = [wintypes.HWND]
        u32.IsWindow.restype = wintypes.BOOL
        u32.IsIconic.argtypes = [wintypes.HWND]
        u32.IsIconic.restype = wintypes.BOOL
        u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u32.GetWindowTextLengthW.restype = ctypes.c_int
        u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u32.GetWindowTextW.restype = ctypes.c_int
        u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u32.GetClassNameW.restype = ctypes.c_int
        u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u32.GetWindowRect.restype = wintypes.BOOL
        u32.SetForegroundWindow.argtypes = [wintypes.HWND]
        u32.SetForegroundWindow.restype = wintypes.BOOL
        u32.GetForegroundWindow.restype = wintypes.HWND
        u32.BringWindowToTop.argtypes = [wintypes.HWND]
        u32.BringWindowToTop.restype = wintypes.BOOL
        u32.SetFocus.argtypes = [wintypes.HWND]
        u32.SetFocus.restype = wintypes.HWND
        u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u32.ShowWindow.restype = wintypes.BOOL
        u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        u32.AttachThreadInput.restype = wintypes.BOOL
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u32.GetWindowThreadProcessId.restype = wintypes.DWORD
        u32.GetSystemMetrics.argtypes = [ctypes.c_int]
        u32.GetSystemMetrics.restype = ctypes.c_int
        u32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        u32.SetCursorPos.restype = wintypes.BOOL
        u32.WindowFromPoint.argtypes = [wintypes.POINT]
        u32.WindowFromPoint.restype = wintypes.HWND
        u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u32.GetAncestor.restype = wintypes.HWND
        u32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u32.GetClientRect.restype = wintypes.BOOL
        u32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        u32.ScreenToClient.restype = wintypes.BOOL
        u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.PostMessageW.restype = wintypes.BOOL
        u32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
        u32.SendInput.restype = wintypes.UINT
        u32.GetDC.argtypes = [wintypes.HWND]
        u32.GetDC.restype = wintypes.HDC
        u32.GetWindowDC.argtypes = [wintypes.HWND]
        u32.GetWindowDC.restype = wintypes.HDC
        u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        u32.ReleaseDC.restype = ctypes.c_int
        u32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        u32.PrintWindow.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.GetCurrentThreadId.restype = wintypes.DWORD
        g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g32.CreateCompatibleDC.restype = wintypes.HDC
        g32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        g32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g32.SelectObject.restype = wintypes.HGDIOBJ
        g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g32.DeleteObject.restype = wintypes.BOOL
        g32.DeleteDC.argtypes = [wintypes.HDC]
        g32.DeleteDC.restype = wintypes.BOOL
        g32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                               wintypes.DWORD]
        g32.BitBlt.restype = wintypes.BOOL
        g32.StretchBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        g32.StretchBlt.restype = wintypes.BOOL
        g32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
        g32.SetStretchBltMode.restype = ctypes.c_int
        g32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                  ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), wintypes.UINT]
        g32.GetDIBits.restype = ctypes.c_int
        if self.dwm is not None:
            self.dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                       ctypes.c_void_p, wintypes.DWORD]
            self.dwm.DwmGetWindowAttribute.restype = ctypes.c_long

    # -- process / window discovery ---------------------------------------
    def _process_path(self, pid: int) -> str:
        if not pid:
            return ""
        process = self.k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if self.k32.QueryFullProcessImageNameW(process, 0, buf, ctypes.byref(size)):
                return buf.value
        finally:
            self.k32.CloseHandle(process)
        return ""

    def _enumerate(self) -> list[dict[str, Any]]:
        windows: list[dict[str, Any]] = []
        own_pid = os.getpid()
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @enum_proc
        def callback(hwnd: int, _lparam: int) -> bool:
            if not self.u32.IsWindowVisible(hwnd):
                return True
            length = self.u32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(min(length + 1, 1000))
            self.u32.GetWindowTextW(hwnd, buf, len(buf))
            title = buf.value.strip()
            if not title:
                return True
            pid = wintypes.DWORD(0)
            self.u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) == own_pid:
                return True
            exe_path = self._process_path(pid.value)
            exe = os.path.basename(exe_path).casefold()
            if exe in _SHELL_EXES:
                return True
            if not window_matches(title, exe_path, custom_keywords=self.custom_keywords,
                                  keywords=self.keywords):
                return True
            class_buf = ctypes.create_unicode_buffer(256)
            self.u32.GetClassNameW(hwnd, class_buf, len(class_buf))
            windows.append({"id": int(hwnd), "title": title[:180], "exe_path": exe_path,
                            "exe": os.path.basename(exe_path), "class": class_buf.value,
                            "pid": int(pid.value)})
            return True

        self.u32.EnumWindows(callback, 0)
        return windows

    def list_windows(self) -> list[dict[str, Any]]:
        with self.lock:
            windows = self._enumerate()
            if self.mode == "window":
                self.tracker.resolve(windows)
            return [{"id": w["id"], "title": w["title"], "exe": w["exe"],
                     "selected": self.mode == "window" and w["id"] == self.tracker.current_id}
                    for w in windows]

    # -- geometry ----------------------------------------------------------
    def _window_rects(self, hwnd: int) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
        full = wintypes.RECT()
        if not self.u32.GetWindowRect(hwnd, ctypes.byref(full)):
            raise TargetError("Windows 无法获取目标窗口坐标")
        visible = wintypes.RECT(full.left, full.top, full.right, full.bottom)
        if self.dwm is not None:
            extended = wintypes.RECT()
            hr = self.dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                                ctypes.byref(extended), ctypes.sizeof(extended))
            if hr == 0 and extended.right > extended.left and extended.bottom > extended.top:
                visible = extended
        return ((int(full.left), int(full.top), int(full.right - full.left), int(full.bottom - full.top)),
                (int(visible.left), int(visible.top),
                 int(visible.right - visible.left), int(visible.bottom - visible.top)))

    def _virtual_screen_rect(self) -> tuple[int, int, int, int]:
        x = int(self.u32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        y = int(self.u32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        w = int(self.u32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        h = int(self.u32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        if w <= 0 or h <= 0:
            w = int(self.u32.GetSystemMetrics(0))
            h = int(self.u32.GetSystemMetrics(1))
            x = y = 0
        if w <= 0 or h <= 0:
            raise TargetError("Windows 无法获取桌面尺寸")
        return x, y, w, h

    def _check_window_state(self, hwnd: int) -> None:
        if not self.u32.IsWindow(hwnd):
            raise TargetError("ChatGPT 窗口已退出或正在重新登录；正在等待重新发现（无需重新配对）")
        if not self.u32.IsWindowVisible(hwnd):
            raise TargetError("目标窗口当前不可见，请在电脑上恢复它")
        if self.u32.IsIconic(hwnd):
            raise TargetError("目标窗口已最小化，请在电脑上将它恢复")
        if self.dwm is not None:
            cloaked = wintypes.DWORD(0)
            hr = self.dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED,
                                                ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if hr == 0 and int(cloaked.value):
                raise TargetError("目标窗口在另一个虚拟桌面或被系统隐藏，请在电脑上切换回来")

    def _resolve_target(self) -> int:
        target = self.tracker.resolve(self._enumerate())
        if target:
            self._check_window_state(target)
            return target
        if self.tracker.status == "ambiguous":
            raise TargetError("找到多个相似窗口，已暂停输入；请重新选择 ChatGPT 窗口")
        if self.tracker.status == "not_selected":
            raise TargetError("请先在手机上选择 ChatGPT 窗口")
        raise TargetError("ChatGPT 窗口已退出或正在重新登录；正在等待重新发现（无需重新配对）")

    def _rect(self) -> tuple[int, int, int, int]:
        with self.lock:
            if self.mode == "desktop":
                return self._virtual_screen_rect()
            target = self._resolve_target()
            _, visible = self._window_rects(target)
            x, y, w, h = visible
            if w < 80 or h < 80 or w * h > 25_000_000:
                raise TargetError("目标窗口尺寸无效或过大")
            return x, y, w, h

    # -- capture -----------------------------------------------------------
    def _dib_to_image(self, mem_dc: int, bitmap: int, width: int, height: int):
        from PIL import Image

        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        copied = self.g32.GetDIBits(mem_dc, bitmap, 0, height, buffer,
                                    ctypes.byref(info), 0)
        if copied == 0:
            raise Win32Error("GetDIBits 失败")
        raw = bytes(buffer)
        return Image.frombuffer("RGBA", (width, height), raw, "raw", "BGRA", 0, 1).convert("RGB")

    @staticmethod
    def _is_black(image) -> bool:
        try:
            return image.convert("L").getbbox() is None
        except Exception:
            return False

    def _stretch_crop(self, source_dc: int, sx: int, sy: int, sw: int, sh: int,
                      target_w: int, target_h: int):
        """Copy + scale a rectangle from source_dc into a small DIB image.

        Scaling happens in GDI so only the small bitmap ever crosses into
        Python; a full 4K window would otherwise cost ~15 MB per frame.
        """
        from PIL import Image  # noqa: F401

        out_dc = out_bitmap = old = None
        try:
            out_dc = self.g32.CreateCompatibleDC(source_dc)
            out_bitmap = self.g32.CreateCompatibleBitmap(source_dc, target_w, target_h)
            if not out_dc or not out_bitmap:
                raise Win32Error("无法创建缩略抓取位图")
            old = self.g32.SelectObject(out_dc, out_bitmap)
            self.g32.SetStretchBltMode(out_dc, 4)  # HALFTONE
            if not self.g32.StretchBlt(out_dc, 0, 0, target_w, target_h,
                                       source_dc, sx, sy, sw, sh, SRCCOPY):
                raise Win32Error("StretchBlt 失败")
            return self._dib_to_image(out_dc, out_bitmap, target_w, target_h)
        finally:
            if out_dc:
                if old:
                    self.g32.SelectObject(out_dc, old)
                if out_bitmap:
                    self.g32.DeleteObject(out_bitmap)
                self.g32.DeleteDC(out_dc)

    def _window_title(self, hwnd: int) -> str:
        try:
            length = self.u32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return ""
            buffer = ctypes.create_unicode_buffer(min(length + 1, 512))
            self.u32.GetWindowTextW(hwnd, buffer, len(buffer))
            return buffer.value.strip()
        except Exception:
            return ""

    def _wgc_frame(self, hwnd: int, max_width: int):
        """Newest Windows Graphics Capture frame, or None to use PrintWindow.

        WGC only produces frames when the window content changes, so the hub
        keeps polling and this returns the cached frame in between. Any
        failure (missing dependency, window mismatch, minimized window) drops
        back to the GDI path for that capture.
        """
        if self._wgc_failed or os.environ.get("RM_CAPTURE") == "screen":
            return None
        # A failed start is retried later instead of disabling WGC for the
        # whole process; windows appear and disappear constantly.
        if time.monotonic() < self._wgc_retry_at:
            return None
        title = self._window_title(hwnd)
        if not title:
            return None
        if self._wgc is None or getattr(self._wgc, "hwnd", 0) != hwnd:
            if self._wgc is not None:
                self._wgc.stop()
                self._wgc = None
            try:
                from wgc_capture import WgcCapture
                self._wgc = WgcCapture(title, max_width, hwnd)
                self._wgc.start()
                self._wgc_title = title
            except Exception:
                self._wgc = None
                self._wgc_retry_at = time.monotonic() + 30
                return None
        image = self._wgc.latest()
        if image is None:
            return None
        size = self._wgc.frame_size()
        full, visible = self._window_rects(hwnd)
        width, height = full[2] - full[0], full[3] - full[1]
        if size and width > 0 and abs(size[0] - width) > max(48, width * 0.05):
            return None
        vw, vh = visible[2] - visible[0], visible[3] - visible[1]
        if width > 0 and height > 0 and vw >= 80 and vh >= 80:
            scale = image.width / width
            left = max(0, round((visible[0] - full[0]) * scale))
            top = max(0, round((visible[1] - full[1]) * scale))
            right = min(image.width, round((visible[2] - full[0]) * scale))
            bottom = min(image.height, round((visible[3] - full[1]) * scale))
            if right - left >= 80 and bottom - top >= 80:
                image = image.crop((left, top, right, bottom))
        self.last_capture_method = "wgc"
        image.info["rm_capture_method"] = "wgc"
        return image

    def _grab_window(self, hwnd: int, max_width: int = 1280):
        captured = self._wgc_frame(hwnd, max_width)
        if captured is not None:
            return captured
        full, visible = self._window_rects(hwnd)
        fx, fy, fw, fh = full
        vx, vy, vw, vh = visible
        if fw <= 0 or fh <= 0:
            raise TargetError("目标窗口尺寸无效")
        left = max(0, vx - fx)
        top = max(0, vy - fy)
        crop_w = min(fw - left, vw)
        crop_h = min(fh - top, vh)
        if crop_w < 80 or crop_h < 80:
            raise TargetError("目标窗口尺寸无效")
        max_width = max(320, min(2560, int(max_width or 1280)))
        if crop_w > max_width:
            target_w = max_width
            target_h = max(1, round(crop_h * target_w / crop_w))
        else:
            target_w, target_h = crop_w, crop_h

        screen_dc = self.u32.GetWindowDC(hwnd)
        if not screen_dc:
            raise Win32Error("无法获取窗口 DC")
        mem_dc = bitmap = old = None
        try:
            mem_dc = self.g32.CreateCompatibleDC(screen_dc)
            bitmap = self.g32.CreateCompatibleBitmap(screen_dc, fw, fh)
            if not mem_dc or not bitmap:
                raise Win32Error("无法创建抓取位图")
            old = self.g32.SelectObject(mem_dc, bitmap)
            method = "printwindow"
            ok = self.u32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
            image = self._stretch_crop(mem_dc, left, top, crop_w, crop_h, target_w, target_h)
            if not ok or self._is_black(image) or os.environ.get("RM_CAPTURE") == "screen":
                method = "screen"
                if not self.g32.BitBlt(mem_dc, 0, 0, fw, fh, screen_dc, 0, 0,
                                       SRCCOPY | CAPTUREBLT):
                    raise Win32Error("PrintWindow 与 BitBlt 均失败")
                image = self._stretch_crop(mem_dc, left, top, crop_w, crop_h,
                                           target_w, target_h)
            self.last_capture_method = method
            # Bind provenance to this image. A concurrent capture may change
            # last_capture_method before this image is encoded or published.
            image.info["rm_capture_method"] = method
            image.info["rm_capture_rect"] = (vx, vy, crop_w, crop_h)
            return image
        finally:
            if mem_dc:
                if old:
                    self.g32.SelectObject(mem_dc, old)
                if bitmap:
                    self.g32.DeleteObject(bitmap)
                self.g32.DeleteDC(mem_dc)
            self.u32.ReleaseDC(hwnd, screen_dc)

    def _grab_screen(self, max_width: int = 1280):
        x, y, w, h = self._virtual_screen_rect()
        max_width = max(320, min(2560, int(max_width or 1280)))
        if w > max_width:
            target_w = max_width
            target_h = max(1, round(h * target_w / w))
        else:
            target_w, target_h = w, h
        screen_dc = self.u32.GetDC(0)
        if not screen_dc:
            raise Win32Error("无法获取屏幕 DC")
        try:
            self.last_capture_method = "screen"
            image = self._stretch_crop(screen_dc, x, y, w, h, target_w, target_h)
            image.info["rm_capture_method"] = "screen"
            return image
        finally:
            self.u32.ReleaseDC(0, screen_dc)

    def capture_meta(self, max_width: int = 1280, quality: int = 68) -> dict[str, Any]:
        with self.lock:
            mode = self.mode
            target = self._resolve_target() if mode != "desktop" else 0
            epoch = self.tracker.epoch
        # Capture/encoding run outside the input lock. Validate the snapshot
        # again so an old window can never be published as the new target.
        if mode == "desktop":
            image = self._grab_screen(min(int(max_width), 1000))
            quality = min(int(quality), 60)
        else:
            image = self._grab_window(target, max_width)
        capture_method = getattr(image, "info", {}).get("rm_capture_method", "unknown")
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=max(30, min(90, int(quality))), optimize=False)
        captured_ms = int(time.time() * 1000)
        with self.lock:
            if self.tracker.epoch != epoch or self.mode != mode:
                raise TargetError("目标窗口已经变化，正在同步新画面")
            self.last_capture_ms = captured_ms
            return {"jpeg": out.getvalue(), "image": image, "epoch": epoch, "mode": mode,
                    "captured_ms": captured_ms, "method": capture_method,
                    "width": image.width, "height": image.height,
                    "pointer_target": (target, image.info["rm_capture_rect"])
                    if capture_method == "printwindow" and "rm_capture_rect" in image.info else None}

    def capture(self) -> bytes:  # compatibility helper
        return self.capture_meta()["jpeg"]

    # -- state / selection -------------------------------------------------
    def info(self) -> dict[str, Any]:
        with self.lock:
            windows = self.list_windows()
            return {"windows": windows, "mode": self.mode,
                    "status": "desktop" if self.mode == "desktop" else self.tracker.status,
                    "epoch": self.tracker.epoch,
                    "frozen": self.frozen_info_locked(),
                    "capture": {"method": self.last_capture_method,
                                "last_ms": self.last_capture_ms}}

    def select(self, window_id: int) -> None:
        with self.lock:
            windows = self._enumerate()
            found = next((w for w in windows if w["id"] == window_id), None)
            if not found:
                raise InputError("找不到该窗口（可能已关闭），请刷新列表重新选择")
            self.tracker.choose(found)
            self.mode = "window"

    def desktop(self) -> None:
        with self.lock:
            if self.mode != "desktop":
                self.mode = "desktop"
                self.tracker.epoch += 1

    def window_mode(self) -> None:
        with self.lock:
            if self.mode != "window":
                self.mode = "window"
                self.tracker.epoch += 1
            self.tracker.resolve(self._enumerate())

    # -- freeze ------------------------------------------------------------
    def freeze(self, seconds: float = 0.0, reason: str = "manual") -> None:
        with self.lock:
            self.frozen_until = (time.monotonic() + seconds) if seconds > 0 else float("inf")
            self.frozen_reason = reason or "manual"
            self.tracker.epoch += 1

    def unfreeze(self) -> None:
        with self.lock:
            self.frozen_until = 0.0
            self.frozen_reason = ""

    def frozen_info_locked(self) -> dict[str, Any]:
        active = self.frozen_until > 0 and (self.frozen_until == float("inf") or
                                            time.monotonic() < self.frozen_until)
        if self.frozen_until > 0 and not active and self.frozen_until != float("inf"):
            self.frozen_until = 0.0
            self.frozen_reason = ""
        until_ms = 0
        if active and self.frozen_until != float("inf"):
            until_ms = int((time.time() + max(0.0, self.frozen_until - time.monotonic())) * 1000)
        return {"active": bool(active), "until_ms": until_ms,
                "reason": self.frozen_reason if active else ""}

    def frozen_info(self) -> dict[str, Any]:
        with self.lock:
            return self.frozen_info_locked()

    # -- input -------------------------------------------------------------
    def _reader(self):
        if self.conversation_reader is None:
            from conversation import ConversationReader
            self.conversation_reader = ConversationReader()
            from attachments import warm_clipboard
            threading.Thread(target=warm_clipboard, name='clipboard-warmup', daemon=True).start()
        return self.conversation_reader

    def read_conversation(self) -> dict[str, Any]:
        with self.lock:
            if self.mode == "desktop":
                raise TargetError("请返回窗口模式后读取对话")
            target = self._resolve_target()
            epoch = self.tracker.epoch
            reader = self._reader()
        result = reader.read(target)
        with self.lock:
            if self.mode != "window" or self.tracker.epoch != epoch or self.tracker.current_id != target:
                raise TargetError("目标窗口已经变化，正在同步对话", "conversation_changed")
            return {**result, "epoch": epoch, "frozen": self.frozen_info_locked(),
                    "status": self.tracker.status, "mode": self.mode}

    def navigation(self) -> dict[str, Any]:
        with self.lock:
            if self.mode != "window":
                raise TargetError("请返回窗口模式后切换对话")
            target, epoch = self._resolve_target(), self.tracker.epoch
            reader = self._reader()
        result = reader.navigation(target)
        with self.lock:
            if self.mode != "window" or self.tracker.epoch != epoch or self.tracker.current_id != target:
                raise TargetError("目标窗口已经变化，请刷新项目与对话列表")
            return {**result, "epoch": epoch}

    def queue(self) -> dict[str, Any]:
        with self.lock:
            if self.mode != 'window':
                raise TargetError('请返回窗口模式后管理队列')
            target, epoch = self._resolve_target(), self.tracker.epoch
            reader = self._reader()
        result = reader.queue(target)
        with self.lock:
            if self.mode != 'window' or self.tracker.epoch != epoch or self.tracker.current_id != target:
                raise TargetError('目标窗口已变化，请刷新队列')
            return {**result, 'epoch': epoch}

    def read_draft(self) -> dict[str, Any]:
        with self.lock:
            if self.mode != "window":
                raise TargetError("请返回窗口模式后处理电脑草稿")
            target, epoch = self._resolve_target(), self.tracker.epoch
            reader = self._reader()
        result = reader.draft(target)
        with self.lock:
            if self.mode != "window" or self.tracker.epoch != epoch or self.tracker.current_id != target:
                raise TargetError("目标窗口已经变化，请重新查看电脑草稿")
            return {**result, "epoch": epoch}

    def composer_options(self) -> dict[str, Any]:
        with self.lock:
            if self.mode != "window":
                raise TargetError("请返回窗口模式后调整对话选项")
            target, epoch = self._resolve_target(), self.tracker.epoch
            reader = self._reader()
        result = reader.composer_options(target)
        with self.lock:
            if self.mode != "window" or self.tracker.epoch != epoch or self.tracker.current_id != target:
                raise TargetError("目标窗口已经变化，请刷新对话选项")
            return {**result, "epoch": epoch}

    def composer_surface(self, key: str, target_id: str) -> dict[str, Any]:
        with self.lock:
            if self.mode != 'window':
                raise TargetError('请返回窗口模式后调整对话选项')
            target, epoch = self._resolve_target(), self.tracker.epoch
            source = self._reader().composer_surface(target, key, target_id)
            _, visible = self._window_rects(target)
        r, crop = source['rect'], source['crop']
        vx, vy, vw, vh = visible
        if not (all(isinstance(part.get(k), (int, float)) and math.isfinite(part[k]) for part in (r, crop) for k in ('x','y','width','height'))
                and 0 < crop['width'] <= 1600 and 0 < crop['height'] <= 1200 and crop['x'] >= vx and crop['y'] >= vy
                and r['width'] > 0 and r['height'] > 0 and r['x'] >= crop['x'] and r['y'] >= crop['y']
                and r['x']+r['width'] <= crop['x']+crop['width'] and r['y']+r['height'] <= crop['y']+crop['height']
                and crop['x'] + crop['width'] <= vx + vw and crop['y'] + crop['height'] <= vy + vh):
            raise TargetError('当前菜单超出窗口，请在画面中操作')
        image = self._grab_window(target, max_width=2560)
        capture_method = getattr(image, "info", {}).get("rm_capture_method", "unknown")
        scale = image.width / vw
        left, top = (crop['x'] - vx) * scale, (crop['y'] - vy) * scale
        image = image.crop((round(left), round(top), round(left + crop['width'] * scale), round(top + crop['height'] * scale)))
        output = io.BytesIO(); image.save(output, format='PNG')
        with self.lock:
            if self.mode != 'window' or self.tracker.epoch != epoch or self.tracker.current_id != target:
                raise TargetError('目标窗口已变化，请重新打开选项')
        return {'epoch': epoch, 'mode': 'window', 'method': capture_method,
                'key': key, 'targetId': target_id, 'image': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii'),
                'bounds': {'x': (r['x']-crop['x'])/crop['width'], 'y': (r['y']-crop['y'])/crop['height'],
                           'width': r['width']/crop['width'], 'height': r['height']/crop['height']}}

    def close(self) -> None:
        if self.conversation_reader:
            self.conversation_reader.close()
        from attachments import close_clipboard
        close_clipboard()

    def attach_files(self, paths, conversation_key: str, epoch: int) -> dict:
        from attachments import paste_files, UploadError
        with self.lock:
            invoked=False
            try:
                if self.frozen_info_locked()['active']:raise TargetError('操作已冻结，文件尚未附加')
                if self.mode!='window' or epoch!=self.tracker.epoch:raise TargetError('电脑窗口已变化，文件尚未附加')
                target=self._resolve_target();self._focus(target)
                reader=self._reader()
                reader.attachment_focus(target,conversation_key)
                def paste():
                    nonlocal invoked
                    self._focus(target)
                    before=reader.attachment_focus(target,conversation_key)
                    if int(self.u32.GetForegroundWindow() or 0)!=target:
                        self._focus(target)
                        before=reader.attachment_focus(target,conversation_key)
                    if int(self.u32.GetForegroundWindow() or 0)!=target:raise TargetError('电脑焦点已变化，文件尚未附加')
                    invoked=True
                    self._key_chord(0x11,0x56)
                    try:return reader.confirm_attachments(target,before,[path.name for path in paths])
                    except TargetError as exc:
                        exc.attachment_before=before
                        raise
                return paste_files(paths,paste)
            except (TargetError,UploadError) as exc:
                if not invoked:exc.code='attachment_not_started'
                raise

    def attached_files_ready(self, before: dict, count: int, epoch: int) -> bool:
        with self.lock:
            if self.mode!='window' or epoch!=self.tracker.epoch:raise TargetError('电脑窗口已切换，请返回附加文件的窗口','attachment_context_changed')
            target=self._resolve_target()
            return self._reader().attachments_ready(target,before,count)

    def _focus(self, hwnd: int) -> None:
        if self.mode == "desktop":
            return
        fg = int(self.u32.GetForegroundWindow() or 0)
        if fg == hwnd:
            return
        if self.u32.IsIconic(hwnd):
            self.u32.ShowWindow(hwnd, SW_RESTORE)
        self.u32.SetForegroundWindow(hwnd)
        if int(self.u32.GetForegroundWindow() or 0) == hwnd:
            return
        # Windows rejects foreground changes from background processes; the
        # ALT-key trick releases the foreground lock in many cases.
        try:
            self._key(VK_MENU, True)
            self._key(VK_MENU, False)
        except RuntimeError:
            pass
        self.u32.SetForegroundWindow(hwnd)
        if int(self.u32.GetForegroundWindow() or 0) == hwnd:
            return
        fg_thread = int(self.u32.GetWindowThreadProcessId(fg, None)) if fg else 0
        current_thread = int(self.k32.GetCurrentThreadId())
        attached = False
        if fg_thread and fg_thread != current_thread:
            attached = bool(self.u32.AttachThreadInput(current_thread, fg_thread, True))
        try:
            self.u32.BringWindowToTop(hwnd)
            self.u32.SetForegroundWindow(hwnd)
            self.u32.SetFocus(hwnd)
        finally:
            if attached:
                self.u32.AttachThreadInput(current_thread, fg_thread, False)
        if int(self.u32.GetForegroundWindow() or 0) != hwnd:
            raise TargetError("Windows 拒绝切换窗口焦点；请先在电脑上点一下 ChatGPT 再操作")

    def _post_window_pointer(self, hwnd: int, rect: tuple, command: dict) -> None:
        if self.mode != 'window' or command.get('pointer_target') != (hwnd, rect):
            raise TargetError('窗口位置已变化，请等画面更新后再点。', 'pointer_frame_changed')
        u32 = self.u32
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if ntpath.basename(self._process_path(pid.value)).casefold() not in _APP_EXES:
            raise TargetError('请先选择 ChatGPT 或 Codex 桌面窗口。', 'pointer_window_required')
        x, y, width, height = rect
        point = wintypes.POINT(x + min(width-1, int(command['x']*width)),
                              y + min(height-1, int(command['y']*height)))
        hit = u32.WindowFromPoint(point)
        if not hit or int(u32.GetAncestor(hit, GA_ROOT) or 0) != hwnd:
            raise TargetError('点击位置被其他窗口挡住，请把 ChatGPT 放到前面。', 'pointer_window_covered')
        client = wintypes.RECT()
        if (not u32.ScreenToClient(hwnd, ctypes.byref(point)) or not u32.GetClientRect(hwnd, ctypes.byref(client))
                or not (0 <= point.x < min(client.right, 32768) and 0 <= point.y < min(client.bottom, 32768))):
            raise TargetError('请点击窗口内容区域。', 'pointer_outside_client')
        packed = point.x | (point.y << 16)
        down, up, button = WINDOW_BUTTONS[command['operation']]
        # Target a specific window procedure. This never injects a desktop
        # mouse event or moves the physical cursor to a remote coordinate.
        for message, flags in ((WM_MOUSEMOVE, 0), (down, button), (up, 0)):
            if not u32.PostMessageW(hwnd, message, flags, packed):
                raise TargetError('Windows 未能接收此次点击，请查看电脑窗口。', 'pointer_unconfirmed')

    def action(self, command: dict[str, Any]) -> dict | None:
        kind = command.get("kind")
        if kind == 'queue' and (not isinstance(command.get('queueKey'), str) or not command['queueKey']
                or not isinstance(command.get('targetId'), str) or not command['targetId']):
            raise InputError('队列操作参数无效，请刷新队列')
        if kind == "draft" and (command.get("operation") != "clear"
                or not isinstance(command.get("conversationKey"), str)
                or not isinstance(command.get("draftRevision"), str) or not command["draftRevision"]):
            raise InputError("草稿处理参数无效，请先查看电脑草稿")
        if kind == "navigate" and (not isinstance(command.get("navigationKey"), str)
                or not isinstance(command.get("targetId"), str) or type(command.get("expanded", True)) is not bool):
            raise InputError("项目与对话选择参数无效")
        if kind == "composer" and (command.get("operation") not in ("invoke", "dismiss", "tap")
                or not isinstance(command.get("optionsKey"), str) or not command["optionsKey"]
                or (command.get("operation") != "dismiss" and not isinstance(command.get("targetId"), str))):
            raise InputError("对话选项参数无效，请刷新后重新选择")
        if kind == 'composer' and command.get('operation') == 'tap':
            if any(type(command.get(axis)) not in (int, float) or not math.isfinite(command[axis]) or not 0 <= command[axis] <= 1 for axis in ('x', 'y')):
                raise InputError('局部操作坐标无效')
        if kind in ("text", "message"):
            text = command.get("text", "")
            if not isinstance(text, str) or len(text) > 6000 or "\0" in text:
                raise InputError("请输入不超过 6000 字符的文本，且不能包含空字符")
            if type(command.get("submit", False)) is not bool:
                raise InputError("submit 必须是布尔值")
            submit_method = command.get("submitMethod", "button")
            if submit_method not in ("button", "CTRL+ENTER", "ENTER"):
                raise InputError("发送方式无效")
            if kind == "message" and ((not text.strip() and not command.get('attachmentsOnly')) or not isinstance(command.get("conversationKey"), str)):
                raise InputError("缺少消息或对话标识")
            if "draftRevision" in command and (not isinstance(command["draftRevision"], str)
                    or not command["draftRevision"] or not isinstance(command.get("conversationKey"), str)):
                raise InputError("缺少已查看的草稿版本，请重新查看电脑草稿")
        with self.lock:
            if self.frozen_info_locked()["active"]:
                reason = self.frozen_info_locked()["reason"]
                raise TargetError(f"输入已冻结（{reason}），请先解冻")
            x, y, w, h = self._rect()
            if command.get("epoch") != self.tracker.epoch:
                raise TargetError("目标窗口已经变化，已阻止旧指令；请刷新画面后重试")
            if kind == 'window-pointer':
                self._post_window_pointer(self.tracker.current_id, (x, y, w, h), command)
                return
            if kind == 'queue':
                if self.mode != 'window':
                    raise TargetError('请返回窗口模式后管理队列')
                if command['targetId'].endswith('|edit'):
                    self._focus(self.tracker.current_id)
                result = self._reader().queue_action(self.tracker.current_id, command['queueKey'], command['targetId'])
                for field in ('queue', 'queueDraft'):
                    if field in result: result[field]['epoch'] = self.tracker.epoch
                return result
            if kind == "composer":
                if self.mode != "window":
                    raise TargetError("请返回窗口模式后调整对话选项")
                self._focus(self.tracker.current_id)
                if command['operation'] == 'tap':
                    r = self._reader().composer_surface(self.tracker.current_id, command['optionsKey'], command['targetId'], consume=True)['rect']
                    if not (all(isinstance(r.get(k), (int, float)) and math.isfinite(r[k]) for k in ('x','y','width','height'))
                            and r['width'] > 0 and r['height'] > 0 and r['x'] >= x and r['y'] >= y and r['x']+r['width'] <= x+w and r['y']+r['height'] <= y+h):
                        raise TargetError('选项已经移出窗口，请刷新后重试')
                    if int(self.u32.GetForegroundWindow() or 0) != self.tracker.current_id:
                        raise TargetError('电脑焦点已变化，请刷新选项')
                    sx=r['x']+min(r['width']-1,command['x']*r['width']);sy=r['y']+min(r['height']-1,command['y']*r['height'])
                    if not self.u32.SetCursorPos(round(sx),round(sy)):
                        raise Win32Error('Windows 拒绝移动鼠标')
                    self._mouse_input(MOUSEEVENTF_LEFTDOWN);self._mouse_input(MOUSEEVENTF_LEFTUP)
                    return
                result = self._reader().composer_action(self.tracker.current_id, command["optionsKey"],
                    command.get("targetId"), dismiss=command["operation"] == "dismiss", refresh=True)
                if result.get('escape'):
                    if int(self.u32.GetForegroundWindow() or 0) != self.tracker.current_id:
                        raise TargetError("电脑焦点已变化，请刷新选项")
                    self._key(0x1B, True)
                    self._key(0x1B, False)
                    result['options'] = self._reader().composer_options(self.tracker.current_id)
                return {"options": {**result['options'], 'epoch': self.tracker.epoch}} if 'options' in result else None
            if kind == "navigate":
                if self.mode != "window":
                    raise TargetError("请返回窗口模式后切换对话")
                try:
                    result=self._reader().navigate(self.tracker.current_id, command["navigationKey"],
                                            command["targetId"], command.get("expanded", True), refresh=True)
                except Exception:
                    self.tracker.epoch += 1
                    raise
                if result['kind'] in ('thread','new') and not result.get('unchanged'):self.tracker.epoch += 1
                return {**({'navigation':{**result['navigation'],'epoch':self.tracker.epoch}} if 'navigation' in result else {}),
                        **({'navigationUnchanged':True} if result.get('unchanged') else {})}
            if kind == "draft":
                if self.mode != "window":
                    raise TargetError("请返回窗口模式后处理电脑草稿")
                self._reader().clear_draft(self.tracker.current_id, command["conversationKey"], command["draftRevision"])
                return
            use_composer = kind == "message" or (kind == "text" and (command.get("draftRevision")
                or (command.get("submit") and submit_method == "button")))
            if use_composer:
                if self.mode != "window":
                    raise TargetError("请返回窗口模式后发送消息")
                if command.get("draftRevision"):
                    self._reader().clear_draft(self.tracker.current_id, command["conversationKey"],
                                               command["draftRevision"], for_send=command.get("submit", False))
                return self._compose_message(command, text, submit_method)
            elif self.mode != "desktop":
                self._focus(self.tracker.current_id)
            if kind in ("tap", "right"):
                try:
                    fx = float(command["x"])
                    fy = float(command["y"])
                except (KeyError, TypeError, ValueError):
                    raise InputError("点击坐标无效")
                if not (0 <= fx <= 1 and 0 <= fy <= 1):
                    raise InputError("坐标必须在 0~1 之间")
                sx = x + min(w - 1, round(fx * w))
                sy = y + min(h - 1, round(fy * h))
                if not self.u32.SetCursorPos(sx, sy):
                    raise Win32Error("Windows 拒绝移动鼠标")
                down, up = ((MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP) if kind == "tap"
                            else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP))
                self._mouse_input(down)
                self._mouse_input(up)
            elif kind == "scroll":
                try:
                    delta = int(command.get("delta", 0))
                except (TypeError, ValueError):
                    raise InputError("滚动量无效")
                if abs(delta) > 1200:
                    raise InputError("滚动量过大")
                self.u32.SetCursorPos(x + w // 2, y + h // 2)
                self._mouse_input(MOUSEEVENTF_WHEEL, delta)
            elif kind in ("text", "message"):
                self._unicode_text(text, submit=command.get("submit", False), submit_key=submit_method)
            elif kind == "key":
                name = str(command.get("key", "")).upper()
                simple = {"ENTER": 0x0D, "ESC": 0x1B, "TAB": 0x09, "BACKSPACE": 0x08}
                combos = {"CTRL+ENTER": (0x11, 0x0D), "CTRL+A": (0x11, 0x41), "CTRL+C": (0x11, 0x43),
                          "CTRL+V": (0x11, 0x56)}
                if name in simple:
                    self._key(simple[name], True)
                    self._key(simple[name], False)
                elif name in combos:
                    modifier, letter = combos[name]
                    self._key(modifier, True)
                    self._key(letter, True)
                    self._key(letter, False)
                    self._key(modifier, False)
                else:
                    raise InputError("未授权的快捷键")
            else:
                raise InputError("不支持的操作")

    def _compose_message(self, command: dict, text: str, submit_method: str):
        def enter_text(use_clipboard=False):
            self._focus(self.tracker.current_id)
            reader = self._reader()
            reuse_text = text if text and command.get("submit") else None
            key = reader.prepare(self.tracker.current_id, command.get("conversationKey"), reuse_text)
            already_present = reader.prepared_text_present is True
            if int(self.u32.GetForegroundWindow() or 0) != self.tracker.current_id:
                # Focusing an accessibility control can move the foreground.
                # No text has been entered: restore once and recheck both the
                # conversation and composer before taking any input action.
                self._focus(self.tracker.current_id)
                key=reader.prepare(self.tracker.current_id,key,reuse_text)
                if already_present and reader.prepared_text_present is not True:
                    raise TargetError("电脑草稿已变化，手机草稿已保留", "send_not_started")
                if int(self.u32.GetForegroundWindow() or 0) != self.tracker.current_id:
                    raise TargetError("电脑焦点已变化，手机草稿已保留")
            if text:
                if reader.prepared_text_present is not True:
                    if use_clipboard:self._key_chord(0x11, 0x56)
                    else:self._unicode_text(text)
                reader.confirm_input(self.tracker.current_id, key, text, submit=command.get("submit", False))
            if not command.get("submit"):
                return
            if submit_method == "button" or reader.prepared_generating or command.get('attachmentsOnly'):
                return {"submission": reader.submit(self.tracker.current_id, key, text)}
            if int(self.u32.GetForegroundWindow() or 0) != self.tracker.current_id:
                raise TargetError("电脑焦点已变化，手机草稿已保留")
            try:
                self._unicode_text("", submit=True, submit_key=submit_method)
            except (TargetError, Win32Error):
                raise TargetError('正在确认发送', 'send_pending') from None
        if len(text)>1024 and not self.u32.GetOpenClipboardWindow():
            from attachments import ClipboardUnavailable, paste_text
            invoked=False
            def paste_once():
                nonlocal invoked
                invoked=True
                return enter_text(True)
            try:return paste_text(text,paste_once)
            except ClipboardUnavailable:
                if invoked:raise
                # No input has occurred. Use one native Unicode input batch.
                return enter_text()
        return enter_text()

    def _send(self, inp: _INPUT) -> None:
        result = self.u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        if result != 1:
            raise TargetError("SendInput 被 Windows 拒绝（可能是管理员/UAC 窗口）")

    def _mouse_input(self, flags: int, data: int = 0) -> None:
        inp = _INPUT()
        inp.type = 0
        inp.u.mi = _MOUSEINPUT(0, 0, ctypes.c_uint32(data).value, flags, 0, 0)
        self._send(inp)

    def _key(self, vk: int, pressed: bool) -> None:
        inp = _INPUT()
        inp.type = 1
        inp.u.ki = _KEYBDINPUT(vk, 0, 0 if pressed else KEYEVENTF_KEYUP, 0, 0)
        self._send(inp)

    def _unicode_text(self, text: str, submit: bool = False, submit_key: str = "ENTER") -> None:
        # UTF-16 code units also handle emoji surrogate pairs.
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        raw = normalized.encode("utf-16-le")
        count = len(raw) + normalized.count("\n") * 2 + ((4 if submit_key == "CTRL+ENTER" else 2) if submit else 0)
        if not count:
            return
        inputs = (_INPUT * count)()
        index = 0
        for offset in range(0, len(raw), 2):
            code_unit = int.from_bytes(raw[offset:offset + 2], "little")
            if code_unit == 10:
                # Chromium ignores a Unicode LF packet. Shift+Enter inserts a
                # line break without activating the application's Send action.
                for vk, flags in ((0x10, 0), (0x0D, 0), (0x0D, KEYEVENTF_KEYUP), (0x10, KEYEVENTF_KEYUP)):
                    inputs[index].type = 1
                    inputs[index].u.ki = _KEYBDINPUT(vk, 0, flags, 0, 0)
                    index += 1
                continue
            for flag in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                inp = inputs[index]
                inp.type = 1
                inp.u.ki = _KEYBDINPUT(0, code_unit, flag, 0, 0)
                index += 1
        if submit:
            if submit_key == "CTRL+ENTER":
                inputs[index].type = 1
                inputs[index].u.ki = _KEYBDINPUT(0x11, 0, 0, 0, 0)
                index += 1
            for flags in (0, KEYEVENTF_KEYUP):
                inputs[index].type = 1
                inputs[index].u.ki = _KEYBDINPUT(0x0D, 0, flags, 0, 0)
                index += 1
            if submit_key == "CTRL+ENTER":
                inputs[index].type = 1
                inputs[index].u.ki = _KEYBDINPUT(0x11, 0, KEYEVENTF_KEYUP, 0, 0)
                index += 1
        result = self.u32.SendInput(count, inputs, ctypes.sizeof(_INPUT))
        if result != count:
            raise TargetError("Windows 未完整接收文字，请检查电脑输入框后再操作")

    def _key_chord(self, modifier: int, key: int) -> None:
        inputs=(_INPUT*4)()
        for index,(vk,flags) in enumerate(((modifier,0),(key,0),(key,KEYEVENTF_KEYUP),(modifier,KEYEVENTF_KEYUP))):
            inputs[index].type=1;inputs[index].u.ki=_KEYBDINPUT(vk,0,flags,0,0)
        if self.u32.SendInput(4,inputs,ctypes.sizeof(_INPUT))!=4:
            self._key(modifier,False)
            raise TargetError('电脑未完整接收粘贴操作')
