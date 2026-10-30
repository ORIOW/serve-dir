"""win_tray.py — Windows 系统托盘图标（纯 ctypes 实现，无第三方依赖）。

设计：
- 托盘图标运行在独立的专用线程里：该线程创建一个 message-only 窗口作为
  Shell_NotifyIcon 的回调窗口，并用自己的 GetMessage 泵维持消息循环——
  完全不触碰 tkinter 的窗口与线程，杜绝跨线程 GUI 调用。
- 回调（左键点击/菜单项）以「普通可调用对象」形式交给调用方，由调用方
  自行决定如何投递回 UI 线程（本项目用主线程队列轮询）。
- 图标优先从指定路径提取（默认传 pythonw.exe → Python 图标），失败回退
  系统默认应用图标。
"""

import ctypes
import os
import sys
import threading
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover - 本模块仅 Windows 可用
    raise ImportError("win_tray 仅支持 Windows")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- 消息常量 ----
WM_NULL = 0x0000
WM_QUIT = 0x0012
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 1
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

# ---- 托盘 API 常量 ----
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO = 0x01
TPM_RETURNCMD, TPM_RIGHTBUTTON, TPM_NONOTIFY = 0x0100, 0x0002, 0x0080
MF_STRING, MF_GRAYED = 0x00, 0x01
IDI_APPLICATION = 32512  # MAKEINTRESOURCE

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.DefWindowProcW.restype = LRESULT
_Shell_NotifyIconW = _shell32.Shell_NotifyIconW
_Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
_Shell_NotifyIconW.restype = wintypes.BOOL

_user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
_user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
]
_shell32.ExtractIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT]
_shell32.ExtractIconW.restype = wintypes.HICON


class TrayIcon:
    """系统托盘图标。回调在托盘线程触发，调用方自行投递回 UI 线程。

    用法：
        tray = TrayIcon(tooltip=..., status_fn=..., icon_path=sys.executable,
                        on_left_click=cb1, menu=(("打开", cb2), ("退出", cb3)))
        tray.start()     # 添加图标并启动消息泵线程
        tray.set_tip(..) / tray.balloon(title, text)
        tray.remove()    # 删除图标并结束消息泵线程
    """

    def __init__(self, tooltip, status_fn=None, icon_path=None,
                 on_left_click=None, menu=()):
        self._tooltip = (tooltip or "serve-dir")[:127]
        self._status_fn = status_fn
        self._icon_path = icon_path
        self._on_left_click = on_left_click
        self._menu = tuple(menu)  # ((label, callback), ...)
        self._cls_name = f"ServeDirTrayWnd{os.getpid()}"
        self._hwnd = None
        self._hicon = None
        self._nid = None
        self._added = False
        self._thread = None
        self._thread_id = 0
        self._wndproc_ref = None  # 持有回调防止 ctypes GC
        self._started = threading.Event()
        self._add_ok = False

    # ---------- 生命周期 ----------

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="serve-dir-tray")
        self._thread.start()
        self._started.wait(timeout=5)
        if not self._add_ok:
            raise RuntimeError("托盘图标添加失败（Shell_NotifyIcon NIM_ADD）")

    def is_added(self):
        return self._added

    @property
    def hwnd(self):
        """托盘回调窗口句柄（message-only），可用于测试注入消息。"""
        return self._hwnd

    def remove(self):
        """删除图标并结束消息泵线程（幂等）。"""
        if self._added and self._nid is not None:
            _Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._added = False
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    # ---------- 状态更新 ----------

    def set_tip(self, text):
        if not self._added or self._nid is None:
            return False
        nid = self._nid
        nid.uFlags = NIF_TIP
        nid.szTip = (text or "")[:127]
        return bool(_Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))

    def balloon(self, title, text):
        if not self._added or self._nid is None:
            return False
        nid = self._nid
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = (title or "")[:63]
        nid.szInfo = (text or "")[:255]
        nid.dwInfoFlags = NIIF_INFO
        try:
            return bool(_Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
        finally:
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP  # 还原常规标志

    # ---------- 内部：托盘线程 ----------

    def _load_icon(self, hinst):
        try:
            if self._icon_path and os.path.isfile(self._icon_path):
                hicon = _shell32.ExtractIconW(hinst, self._icon_path, 0)
                if hicon:
                    return hicon
        except Exception:
            pass
        return _user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

    def _make_nid(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._hicon
        nid.szTip = self._tooltip
        return nid

    def _run(self):
        self._thread_id = _kernel32.GetCurrentThreadId()
        hinst = _kernel32.GetModuleHandleW(None)

        self._wndproc_ref = WNDPROC(self._wndproc)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = self._cls_name
        if not _user32.RegisterClassW(ctypes.byref(wc)):
            self._started.set()
            return

        # parent = HWND_MESSAGE：message-only 窗口，不显示、不进任务栏
        self._hwnd = _user32.CreateWindowExW(
            0, self._cls_name, self._cls_name, 0,
            0, 0, 0, 0, wintypes.HWND(-3), None, hinst, None,
        )
        if not self._hwnd:
            self._started.set()
            return

        self._hicon = self._load_icon(hinst)
        self._nid = self._make_nid()
        self._add_ok = bool(_Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid)))
        self._added = self._add_ok
        self._started.set()

        msg = wintypes.MSG()
        while True:
            r = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:  # WM_QUIT / 错误
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        # 线程退出时其窗口自动销毁

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAYICON:
                lp = lparam & 0xFFFF
                if lp in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    if self._on_left_click:
                        self._on_left_click()
                    return 0
                if lp == WM_RBUTTONUP:
                    self._show_menu(hwnd)
                    return 0
                return 0
        except Exception:
            pass  # 回调异常不得拖垮消息泵
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self, hwnd):
        hmenu = _user32.CreatePopupMenu()
        if not hmenu:
            return
        try:
            if self._status_fn:
                _user32.AppendMenuW(hmenu, MF_STRING | MF_GRAYED, 0, self._status_fn()[:120])
                _user32.AppendMenuW(hmenu, 0x0800, 0, None)  # MF_SEPARATOR
            for i, (label, _cb) in enumerate(self._menu, start=1):
                _user32.AppendMenuW(hmenu, MF_STRING, i, label)
            pt = POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            # TrackPopupMenu 前必须 SetForegroundWindow，否则菜单不随点击外消失
            _user32.SetForegroundWindow(hwnd)
            cmd = _user32.TrackPopupMenu(
                hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
                pt.x, pt.y, 0, hwnd, None,
            )
            _user32.PostMessageW(hwnd, WM_NULL, 0, 0)
            if cmd:
                for i, (_label, cb) in enumerate(self._menu, start=1):
                    if cmd == i:
                        cb()
                        break
        finally:
            _user32.DestroyMenu(hmenu)
