"""sd_macos_check.py — serve-dir macOS 真机验收脚本（零第三方依赖）。

在 Mac 上怎么跑（技能根目录 = 本仓库根目录）：

    cd <技能根目录>
    python3 tests/sd_macos_check.py

逐项输出 PASS / FAIL / SKIP；结尾输出 `FAILED: none`（全部通过/跳过）或失败清单；
退出码 0 = 通过/跳过，1 = 有 FAIL。

检查组：
  G1 Python 版本与平台（darwin，>= 3.9）
  G2 tkinter 可用（能建 Tk 并销毁）
  G3 CLI 全链路：列表 200、/__download zip（testzip 通过）、默认 PUT 403、
     --allow-write 后 PUT 201、--dir 不存在时非零退出
  G4 GUI 可实例化，_setup_tray() 在 macOS 上不抛异常
  G5 菜单栏图标：pyobjc 可用时实测 start()/is_added()/stop()；
     不可用时 SKIP 并打印安装命令（pip3 install pyobjc）
  G6 非 Windows 绑定语义：allow_reuse_address is True
  G7 端口复用：起 → 停 → 立刻同端口再起，应成功

在 Windows/其它平台运行时：优雅跳过 macOS 专属项并正常退出（exit 0）。
"""

import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

IS_MAC = sys.platform == "darwin"
SCRIPTS = os.path.abspath('scripts')
# 以 `python3 tests/sd_macos_check.py` 直跑时 sys.path[0] 是 tests/ 而非技能根目录，
# 必须在首次 import serve_dir / serve_dir_gui 之前把 scripts/ 放进 sys.path。
sys.path.insert(0, SCRIPTS)
fails = []
skips = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else ' | ' + str(detail)))
    if not cond:
        fails.append(name)


def skip(name, reason):
    print('SKIP %s (%s)' % (name, reason))
    skips.append(name)


# 本机测试请求一律直连：系统代理（如 macOS 上的 Clash）会把"连接拒绝"变成代理 502
# 响应，令"端口已停/连不上"类断言误判（代理例外表里的 127.* 通配 urllib 不识别）。
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


def http(method, url, data=None):
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
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


# ---------- G1 平台与版本 ----------

if not IS_MAC:
    skip('G1 platform=darwin', '当前平台为 %s；本脚本用于 macOS 真机验收' % sys.platform)
    print('---')
    print('SKIPPED on this platform — 请在 Mac 上运行：')
    print('    cd <技能根目录>')
    print('    python3 tests/sd_macos_check.py')
    print('FAILED: none（本机全部跳过）')
    sys.exit(0)

check('G1 darwin', IS_MAC)
check('G1 python >= 3.9', sys.version_info >= (3, 9), sys.version)

# ---------- G2 tkinter ----------

try:
    import tkinter as tk
    r = tk.Tk()
    r.withdraw()
    r.update()
    r.destroy()
    check('G2 tkinter', True)
except Exception as e:
    check('G2 tkinter', False, e)

# ---------- G3 CLI 全链路 ----------

import serve_dir  # noqa: E402  （G6 用）
from serve_dir import PlainHandler, ThreadingHTTPServer  # noqa: E402  （G6/G7 用）

td = tempfile.mkdtemp(prefix='sd_macos_check_')
sub = os.path.join(td, 'sub')
os.makedirs(sub)
with open(os.path.join(td, 'file.txt'), 'wb') as f:
    f.write(b'CONTENT')
with open(os.path.join(sub, 'inner.txt'), 'wb') as f:
    f.write(b'INNER')
outside = os.path.join(os.path.dirname(td), 'sd_macos_outside.txt')
with open(outside, 'wb') as f:
    f.write(b'OUTSIDE')

SERVE = os.path.join(SCRIPTS, 'serve_dir.py')


def start_cli(port, extra=()):
    return subprocess.Popen(
        [sys.executable, SERVE, '--dir', td, '--port', str(port),
         '--host', '127.0.0.1'] + list(extra),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


try:
    proc = start_cli(18199, ('--plain',))
    check('G3 server up', wait_up(18199))
    st, body = http('GET', 'http://127.0.0.1:18199/')
    check('G3 home 200 + listing', st == 200 and b'file.txt' in body, (st,))
    st, body = http('GET', 'http://127.0.0.1:18199/__download?names=sub')
    if st == 200:
        zf = zipfile.ZipFile(io.BytesIO(body))
        check('G3 zip download (testzip)', zf.testzip() is None
              and 'sub/inner.txt' in zf.namelist(), zf.namelist())
    else:
        check('G3 zip download (testzip)', False, st)
    check('G3 PUT 403 (read-only default)',
          http('PUT', 'http://127.0.0.1:18199/x.txt', b'x')[0] == 403)
    proc.terminate()
    proc.wait(timeout=5)
    time.sleep(0.3)

    proc = start_cli(18199, ('--allow-write',))
    check('G3 restart --allow-write', wait_up(18199))
    check('G3 PUT 201 (allow-write)',
          http('PUT', 'http://127.0.0.1:18199/up.txt', b'UP')[0] == 201)
    proc.terminate()
    proc.wait(timeout=5)
    time.sleep(0.3)

    rc = subprocess.run(
        [sys.executable, SERVE, '--dir', os.path.join(td, 'no_such_dir'),
         '--port', '18199', '--host', '127.0.0.1'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    check('G3 --dir nonexistent exits nonzero', rc != 0, rc)
finally:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        pass

# ---------- G4 GUI 实例化 + _setup_tray ----------

try:
    import serve_dir_gui  # noqa: E402  （sys.path 已在文件头部包含 scripts/）
except Exception as e:  # tkinter 缺失等：记 FAIL 并跳过 G4，不整体崩溃
    serve_dir_gui = None
    check('G4 import serve_dir_gui', False, e)

if serve_dir_gui is not None:
    try:
        import tkinter as tk  # noqa: E402  （G2 已验证；此处确保同一进程可用）
        root = tk.Tk()
        root.withdraw()
        app = serve_dir_gui.ServeDirGUI(root)
        check('G4 GUI instantiated', window_alive(root))
        app._setup_tray()  # macOS 上不抛异常即通过（pyobjc 缺失时 tray=None 降级）
        check('G4 _setup_tray no exception', True)
        app._quit_from_tray()
    except Exception as e:
        check('G4 GUI instantiate/_setup_tray', False, e)

# ---------- G5 菜单栏图标（pyobjc 可用时实测）----------

try:
    import AppKit  # noqa: F401
    have_pyobjc = True
except Exception:
    have_pyobjc = False

if not have_pyobjc:
    skip('G5 menu bar (pyobjc)', '未安装 pyobjc —— 安装命令: pip3 install pyobjc')
else:
    try:
        from mac_tray import MacTray
        tray = MacTray(tooltip='sd_macos_check',
                       menu=(('打开主窗口', lambda: None), ('退出', lambda: None)))
        tray.start()
        check('G5 tray is_added', tray.is_added())
        tray.set_tip('sd_macos_check running')
        time.sleep(0.5)  # 让菜单栏稳定
        tray.stop()
        check('G5 tray stopped', not tray.is_added())
    except Exception as e:
        check('G5 menu bar', False, e)

# ---------- G6 非 Windows 绑定语义 ----------

check('G6 allow_reuse_address True (non-Windows)',
      sys.platform == 'win32' or ThreadingHTTPServer.allow_reuse_address is True,
      getattr(ThreadingHTTPServer, 'allow_reuse_address', None))

# ---------- G7 端口复用：起 → 停 → 立刻同端口再起 ----------


def start_cli(port):
    return start_cli_raw(port, ())


def start_cli_raw(port, extra):
    return subprocess.Popen(
        [sys.executable, SERVE, '--dir', td, '--port', str(port),
         '--host', '127.0.0.1'] + list(extra),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


try:
    p1 = start_cli(18197)
    check('G7 first start', wait_up(18197))
    p1.terminate()
    p1.wait(timeout=5)
    time.sleep(0.3)
    p2 = start_cli(18197)
    ok2 = wait_up(18197)
    check('G7 immediate rebind same port', ok2)
    p2.terminate()
    p2.wait(timeout=5)
except Exception as e:
    check('G7 port reuse', False, e)

# ---------- 清理与总结 ----------

shutil.rmtree(td, ignore_errors=True)
try:
    os.remove(outside)
except OSError:
    pass

print('---')
if skips:
    print('SKIPPED (%d):' % len(skips))
    for s in skips:
        print('  ' + s)
print('FAILED:', fails if fails else 'none')
sys.exit(1 if fails else 0)
