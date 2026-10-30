import ctypes, os, shutil, socket, sys, time, urllib.request, urllib.error

SCRIPTS = os.path.abspath('scripts')
user32 = ctypes.windll.user32
fails = []
def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else ' | ' + str(detail)))
    if not cond:
        fails.append(name)

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

sys.path.insert(0, SCRIPTS)
import tkinter as tk
import serve_dir_gui
import win_tray

errors = []
root = tk.Tk(); root.withdraw()
app = serve_dir_gui.ServeDirGUI(root)
app._report_error = lambda t, m: errors.append(('err', t, str(m)))
app._report_info = lambda t, m: errors.append(('info', t, str(m)))

# NT1 ????? + message-only ????
check('NT1 tray added', app.tray is not None and app.tray.is_added(), app.tray)
hwnd = app.tray.hwnd
check('NT1 tray window exists', bool(hwnd), hwnd)
found = user32.FindWindowW('ServeDirTrayWnd' + str(os.getpid()), None)
check('NT1 FindWindow finds it', bool(found), found)

# NT2 set_tip / balloon ????
check('NT2 set_tip ok', app.tray.set_tip('serve-dir \u00b7 \u6d4b\u8bd5'))
check('NT2 balloon ok', app.tray.balloon('serve-dir', '\u6d4b\u8bd5\u6c14\u6ce1'))

# NT3 ???? ? ???? = ??? + ????
td = os.path.join(os.environ['TEMP'], 'sd_tray_test')
shutil.rmtree(td, ignore_errors=True); os.makedirs(td)
open(os.path.join(td, 'a.txt'), 'wb').write(b'A')
app.dir_var.set(td); app.port_var.set('18111'); app.start_server()
dl = time.time() + 8
while time.time() < dl:
    try:
        socket.create_connection(('127.0.0.1', 18111), 0.3).close(); break
    except OSError:
        time.sleep(0.15)
check('NT3 server up', True)
app._on_window_close()
check('NT3 window withdrawn', root.state() == 'withdrawn', root.state())
check('NT3 server still serving', http('GET', 'http://127.0.0.1:18111/')[0] == 200)

def pump(sec=0.8):
    dl = time.time() + sec
    while time.time() < dl:
        root.update(); time.sleep(0.02)

# NT4 ???????PostMessage ? ???? ? ??? ? ????
user32.PostMessageW(hwnd, win_tray.WM_TRAYICON, 0, win_tray.WM_LBUTTONUP)
pump(1.0)
check('NT4 left click restores', root.state() == 'normal', root.state())
user32.PostMessageW(hwnd, win_tray.WM_TRAYICON, 0, win_tray.WM_LBUTTONUP)
pump(1.0)
check('NT4 second click hides', root.state() == 'withdrawn', root.state())
app._show_window()

# NT5 ???? ? ?? + ???? + ???
tray_ref = app.tray
app._quit_from_tray()
check('NT5 servers stopped', len(app.servers) == 0, list(app.servers))
check('NT5 18111 down', http('GET', 'http://127.0.0.1:18111/')[0] is None)
check('NT5 tray icon removed', not tray_ref.is_added())
check('NT5 pump thread ended', tray_ref._thread is None)
check('NT5 tooltip after remove no-crash', tray_ref.set_tip('x') is False)

# root destroyed by _quit_from_tray
print('---')
print('FAILED:', fails if fails else 'none')
sys.exit(1 if fails else 0)

