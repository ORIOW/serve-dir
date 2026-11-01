#!/usr/bin/env python3
"""mac_tray.py — macOS 菜单栏图标（pyobjc/AppKit），serve-dir 的托盘等价物。

双模式：

1. 助手进程（`python3 mac_tray.py`）：
   - 本进程主线程运行 NSApplication 事件循环（AppKit 主线程要求在此天然满足）；
     tkinter GUI 因此可以继续独占 GUI 进程的主线程——两个事件循环互不干扰，
     这是选择「助手进程」而非「同进程 NSApplication + tkinter」的核心原因。
   - stdin 行协议（GUI 进程写入，UTF-8）：
       tip <text>              更新悬停提示（tooltip）；菜单栏本体只显示图标
       balloon <title>\\t<text> 通知（降级实现：改为更新悬停提示——macOS 上
                               UNUserNotificationCenter 需要捆绑包，脚本场景
                               不可用；不抛错）
       quit                    结束助手
   - stdout 事件行（GUI 进程读取）：
       ready                   状态项已添加，可以接收命令
       icon <mode>             菜单栏图标形态：sf-symbol / emoji-fallback（诊断用）
       error <reason>          pyobjc 缺失等，无法提供菜单栏
       menu <n>                用户选中菜单第 n 项（1-based；1=切换主窗口显隐，
                               2=打开主窗口，3=退出）
   - stdin EOF（GUI 退出/被杀）→ 助手自动 terminate，不产生孤儿进程。

2. 模块（`from mac_tray import MacTray`）：
   接口对齐 win_tray.TrayIcon：start()/stop()/set_tip()/balloon()/remove()/
   is_added()/hwnd。内部 spawn 助手子进程并起读线程解析事件；事件回调由
   调用方（serve_dir_gui）经 `_post_to_main` 投递回 UI 线程——本模块绝不
   直接触碰 tkinter。

pyobjc 缺失时助手输出 `error pyobjc-missing: ...` 并退出，MacTray.start()
会抛出可捕获的 RuntimeError，GUI 侧捕获后降级为「无菜单栏图标」继续运行。
"""

import os
import subprocess
import sys
import threading

IS_MAC = sys.platform == "darwin"


# ======================================================================
# 助手进程模式（python3 mac_tray.py）——pyobjc 全部在这个分支内导入
# ======================================================================

def _make_status_icon(AppKit):
    """菜单栏图标：SF Symbols「server.rack」——机架服务器（模板图，自动适配深浅色）。

    AppKit 由调用方（_run_helper）传入：本模块刻意不在顶层 import pyobjc，
    而「函数内 import」只绑定局部名——这里若按全局引用会 NameError 且被
    兜底 except 静默吞掉（曾导致图标永远降级为 🖥）。任何失败返回 None。
    """
    try:
        img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "server.rack", "serve-dir")
        if img is None:
            return None
        cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            15, AppKit.NSFontWeightMedium)
        sized = img.imageWithSymbolConfiguration_(cfg)
        if sized is None:
            return None
        sized.setTemplate_(True)
        return sized
    except Exception:
        return None


def _run_helper():
    import threading

    try:
        import AppKit
        from Foundation import NSObject
    except Exception as exc:  # pyobjc 未安装
        print("error pyobjc-missing: %s" % exc, flush=True)
        return

    class Delegate(NSObject):
        """AppKit 主线程回调：菜单/点击事件在此转换为协议行输出。"""

        def menuPick_(self, sender):
            print("menu %d" % (sender.tag() + 1), flush=True)
        # 「退出」不在此实现：走 menu 3 → GUI 回调 _quit_from_tray，
        # 由 GUI 经 stdin「quit」命令让助手干净退出（doQuit 直退会留下孤儿 GUI 窗口）。

    app = AppKit.NSApplication.sharedApplication()
    # Accessory：纯菜单栏应用，不出现 Dock 图标
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
        AppKit.NSVariableStatusItemLength)
    button = item.button()
    icon = _make_status_icon(AppKit)
    if icon is not None:
        button.setImage_(icon)
    else:
        button.setTitle_("🖥")  # server.rack 不可用时的兜底
    button.setToolTip_("serve-dir")  # 状态文字走悬停提示，不占菜单栏
    # 协议诊断行（GUI 忽略未知行）：让测试/排查无需看菜单栏即可知图标形态
    print("icon %s" % ("sf-symbol" if icon is not None else "emoji-fallback"),
          flush=True)

    delegate = Delegate.alloc().init()

    menu = AppKit.NSMenu.alloc().init()
    for tag, title in enumerate(("显示 / 隐藏主窗口", "打开主窗口", "退出")):
        mi = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, "menuPick:", "")
        mi.setTarget_(delegate)
        mi.setTag_(tag)
        menu.addItem_(mi)
    item.setMenu_(menu)  # 单击图标弹出菜单（macOS 标准交互）

    def _terminate():
        AppKit.NSApp().performSelectorOnMainThread_withObject_waitUntilDone_(
            "terminate:", None, False)

    def stdin_reader():
        # UI 变更（setToolTip/terminate）必须发生在 AppKit 主线程：
        # 用 performSelectorOnMainThread 派发，读线程不直接碰 AppKit 对象。
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            if line == "quit":
                _terminate()
                return
            if line.startswith("tip "):
                button.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "setToolTip:", line[4:][:160], False)
            elif line.startswith("balloon "):
                # macOS 26 无捆绑包场景拿不到通知中心 → 降级为悬停提示
                text = line[8:].replace("\t", " · ")
                button.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "setToolTip:", ("🔔 " + text)[:160], False)
                print("balloon-degraded", flush=True)
        # stdin EOF：GUI 进程已退出/被杀 → 助手自杀，不产生孤儿
        _terminate()

    threading.Thread(target=stdin_reader, daemon=True, name="mac-tray-stdin").start()
    print("ready", flush=True)
    app.run()
    # NSApp.run() 返回（terminate）→ 清理状态项
    try:
        AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(item)
    except Exception:
        pass


# ======================================================================
# 模块模式：供 serve_dir_gui 使用
# ======================================================================

class MacTray:
    """macOS 菜单栏图标（通过助手子进程），接口对齐 win_tray.TrayIcon。

    事件回调（on_left_click / menu 项）在助手读线程触发，均为轻量的
    入队操作——调用方负责投递回 UI 线程（serve_dir_gui._post_to_main）。
    """

    def __init__(self, tooltip, status_fn=None, icon_path=None,
                 on_left_click=None, menu=()):
        self._tooltip = (tooltip or "serve-dir")[:120]
        self._status_fn = status_fn  # 状态经 set_tip() 进悬停提示，不占菜单栏
        self._icon_path = icon_path  # 暂不使用：菜单栏显示文字标题
        self._on_left_click = on_left_click
        self._menu = tuple(menu)
        self._proc = None
        self._reader = None
        self._added = False
        self._ready = threading.Event()
        self._error = None
        self._helper_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "mac_tray.py")

    # ---------- 生命周期 ----------

    def start(self):
        if not IS_MAC:
            raise RuntimeError("MacTray 仅支持 macOS")
        if not os.path.isfile(self._helper_path):
            raise RuntimeError("找不到菜单栏助手脚本: %s" % self._helper_path)
        self._proc = subprocess.Popen(
            [sys.executable, self._helper_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            start_new_session=True,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="mac-tray-reader")
        self._reader.start()
        if not self._ready.wait(timeout=8):
            self.stop()
            raise RuntimeError("菜单栏助手 8 秒内未就绪（%s）"
                               % (self._error or "timeout"))
        if self._error:
            self.stop()
            raise RuntimeError("菜单栏助手不可用：%s" % self._error)
        self._added = True
        self.set_tip(self._tooltip)

    def is_added(self):
        return (self._added and self._proc is not None
                and self._proc.poll() is None)

    def stop(self):
        """结束助手（其内部会清理菜单栏状态项）并关闭管道。幂等。"""
        self._send("quit")
        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
        self._proc = None
        was = self._added
        self._added = False
        return was

    def remove(self):
        """对齐 win_tray.TrayIcon.remove()：删除图标 = 结束助手。"""
        return self.stop()

    # ---------- 状态更新 ----------

    def _send(self, line):
        if self._proc is not None and self._proc.stdin and not self._proc.stdin.closed:
            try:
                self._proc.stdin.write(line + "\n")
                self._proc.stdin.flush()
                return True
            except OSError:
                return False
        return False

    def set_tip(self, text):
        return self._send("tip " + (text or ""))

    def balloon(self, title, text):
        # macOS 26 无捆绑包场景拿不到通知中心 → 助手降级为更新菜单栏标题
        return self._send("balloon " + (title or "") + "\t" + (text or ""))

    @property
    def hwnd(self):
        return None  # macOS 无 Windows 句柄概念；测试用 is_added()

    # ---------- 事件读取 ----------

    def _dispatch(self, fn):
        if fn is None:
            return
        try:
            fn()
        except Exception:
            pass  # 回调异常不得拖垮读线程

    def _read_loop(self):
        for line in self._proc.stdout:
            line = line.strip()
            if line == "ready":
                self._ready.set()
            elif line.startswith("error"):
                self._error = line[5:].strip() or "unknown"
                self._ready.set()
            elif line == "menu 1":
                self._dispatch(self._on_left_click)  # 切换主窗口显隐
            elif line.startswith("menu "):
                try:
                    idx = int(line[5:]) - 2  # menu 2 -> menu[0]
                except ValueError:
                    continue
                if 0 <= idx < len(self._menu):
                    self._dispatch(self._menu[idx][1])
            # balloon-degraded / 其它行：忽略


if __name__ == "__main__":
    if not IS_MAC:
        print("error not-macos: mac_tray 助手仅支持 macOS", flush=True)
        sys.exit(0)
    _run_helper()
