"""sd_close_tray_test.py — 回归：窗口 × / 托盘左键 = 仅显隐，退出只走托盘菜单。

覆盖（任务单 2026-10-30-001-gui-close-tray 场景 1-5）：
  S1  托盘可用 + 无实例 + 窗口 ×   -> 不退出、withdrawn、托盘未移除、进程存活
  S2  托盘可用 + 有实例 + 窗口 ×   -> 不退出、withdrawn、实例仍在服务
  S3  托盘可用 + 左键切换（含真实托盘 hwnd 消息注入，若环境允许）-> 永不退出
  S4  托盘右键菜单「退出」完整接线（_post_to_main -> _quit_from_tray）-> 真退出
  S5  托盘不可用（None）+ ×       -> 走刻意降级退出分支（记录调用）
  S6  托盘不可用 + 直接退出       -> 服务停止、窗口销毁
  S7  真实托盘 + 有实例全链路（环境支持时）

注意：在 NIM_ADD 失败的沙箱里 _setup_tray 会降级 self.tray=None；
托盘可用情形用 FakeTray 注入保证两种环境结果一致。
必须以技能根目录为 cwd 运行（依赖 os.path.abspath('scripts')）。
端口使用 18120-18124（避开 18090-18104 的既有回归）。
"""

import ctypes
import os
import shutil
import socket
import sys
import time
import urllib.request
import urllib.error

SCRIPTS = os.path.abspath('scripts')
fails = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else ' | ' + str(detail)))
    if not cond:
        fails.append(name)


def http(method, url, data=None):
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        print('CLIENT-EXC:', type(e).__name__, e)
        return None, str(e).encode()


def wait_up(port, timeout=8):
    dl = time.time() + timeout
    while time.time() < dl:
        try:
            socket.create_connection(('127.0.0.1', port), 0.3).close()
            return True
        except OSError:
            time.sleep(0.15)
    return False


def window_alive(root):
    try:
        return bool(root.winfo_exists())
    except tk.TclError:
        return False


def pump(root, sec=1.0):
    dl = time.time() + sec
    while time.time() < dl:
        if not window_alive(root):
            return
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.02)


sys.path.insert(0, SCRIPTS)
import tkinter as tk
import serve_dir_gui
import win_tray


class FakeTray:
    """托盘可用情形的确定性替身（沙箱 NIM_ADD 可能失败）。"""

    def __init__(self):
        self.removed = False
        self.tips = []
        self.balloons = []
        self.hwnd = None

    def is_added(self):
        return not self.removed

    def set_tip(self, text):
        self.tips.append(text)
        return True

    def balloon(self, title, text):
        self.balloons.append((title, text))
        return True

    def remove(self):
        self.removed = True


base = os.path.join(os.environ['TEMP'], 'sd_close_tray_test')
shutil.rmtree(base, ignore_errors=True)
td = os.path.join(base, 'dir')
os.makedirs(td)
open(os.path.join(td, 'file.txt'), 'wb').write(b'CONTENT')

errors = []


def make_app(port, tray, keep_real=False):
    """新建 GUI 实例。keep_real=True 时保留 __init__ 创建的真实托盘（忽略 tray 参数）。"""
    root = tk.Tk()
    root.withdraw()
    app = serve_dir_gui.ServeDirGUI(root)
    if app.tray is not None and not keep_real:
        try:
            app.tray.remove()  # 释放 __init__ 里创建的真实托盘图标，避免桌面残留
        except Exception:
            pass
    if not keep_real:
        app.tray = tray
    app._report_error = lambda t, m: errors.append(('err', t, str(m)))
    app._report_info = lambda t, m: errors.append(('info', t, str(m)))
    app.dir_var.set(td)
    app.port_var.set(str(port))
    return root, app


# ---------- S1 托盘可用 + 无实例 + × ----------
root, app = make_app(18120, FakeTray())
app._on_window_close()
check('S1 not quit (window alive)', window_alive(root))
check('S1 withdrawn', root.state() == 'withdrawn', root.state())
check('S1 tray not removed', not app.tray.removed)
check('S1 balloon shown once', len(app.tray.balloons) == 1, app.tray.balloons)
app._show_window()
check('S1 restored', root.state() == 'normal')

# ---------- S2 托盘可用 + 有实例 + × ----------
app.port_var.set('18120')
app.start_server()
check('S2 server up', wait_up(18120))
app._on_window_close()
check('S2 withdrawn', root.state() == 'withdrawn', root.state())
check('S2 still serving', http('GET', 'http://127.0.0.1:18120/')[0] == 200)
check('S2 tray not removed', not app.tray.removed)
check('S2 window alive', window_alive(root))
app._show_window()
app.tree.selection_set('18120')
app.stop_selected()
check('S2 instance stopped', 18120 not in app.servers)

# ---------- S3 托盘可用 + 左键切换（永不退出）----------
app._toggle_window()
check('S3 toggle hides', root.state() == 'withdrawn')
check('S3 alive after hide', window_alive(root))
app._toggle_window()
check('S3 toggle shows', root.state() == 'normal')
check('S3 no error popups', not errors, errors)

# S3b 真实托盘 hwnd 全链路消息注入（FakeTray 无 hwnd，环境支持真实托盘时由 S7 覆盖）
if app.tray.hwnd:
    user32 = ctypes.windll.user32
    user32.PostMessageW(app.tray.hwnd, win_tray.WM_TRAYICON, 0, win_tray.WM_LBUTTONUP)
    pump(root, 1.0)
    check('S3b fake-tray click hides', root.state() == 'withdrawn', root.state())
    user32.PostMessageW(app.tray.hwnd, win_tray.WM_TRAYICON, 0, win_tray.WM_LBUTTONUP)
    pump(root, 1.0)
    check('S3b fake-tray click restores', root.state() == 'normal', root.state())

app._quit_from_tray()
check('S3 cleanup destroyed', not window_alive(root))

# ---------- S4 菜单「退出」完整接线 ----------
root2, app2 = make_app(18121, FakeTray())
app2.start_server()
check('S4 server up', wait_up(18121))
tray_ref = app2.tray
app2._post_to_main(app2._quit_from_tray)  # 托盘菜单「退出」项的真实接线
pump(root2, 1.0)
check('S4 instances stopped', 18121 not in app2.servers)
check('S4 tray removed', tray_ref.removed)
check('S4 window destroyed', not window_alive(root2))

# ---------- S5 托盘不可用 + ×：刻意降级分支 ----------
root3, app3 = make_app(18122, None)
calls = []
app3._quit_from_tray = lambda: calls.append(1)  # 仅验证降级分支被走到
app3._on_window_close()
check('S5 degradation branch reached', len(calls) == 1, calls)
root3.destroy()

# ---------- S6 托盘不可用：退出路径完好 ----------
root4, app4 = make_app(18123, None)
app4.start_server()
check('S6 server up', wait_up(18123))
app4._quit_from_tray()
check('S6 server stopped', http('GET', 'http://127.0.0.1:18123/')[0] is None)
check('S6 window destroyed', not window_alive(root4))

# ---------- S7 真实托盘 + 有实例全链路（环境支持时）----------
root5, app5 = make_app(18124, None, keep_real=True)
root5.deiconify()
app5.start_server()
if app5.tray is not None and app5.tray.hwnd:
    user32 = ctypes.windll.user32
    app5._on_window_close()
    check('S7 real-tray withdrawn', root5.state() == 'withdrawn')
    user32.PostMessageW(app5.tray.hwnd, win_tray.WM_TRAYICON, 0, win_tray.WM_LBUTTONUP)
    pump(root5, 1.0)
    check('S7 real-tray click restores', root5.state() == 'normal', root5.state())
    user32.PostMessageW(app5.tray.hwnd, win_tray.WM_TRAYICON, 0, win_tray.WM_LBUTTONUP)
    pump(root5, 1.0)
    check('S7 real-tray click hides again', root5.state() == 'withdrawn', root5.state())
    app5._show_window()
else:
    print('S7 skipped: real tray unavailable (NIM_ADD sandbox limit)')
app5._quit_from_tray()
check('S7 cleanup destroyed', not window_alive(root5))

print('---')
print('FAILED:', fails if fails else 'none')
sys.exit(1 if fails else 0)
