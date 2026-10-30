import ctypes, os, sys, time, urllib.request

LOG = os.path.join(os.environ.get("TEMP", "/tmp"), "sd_tray_pt.log")
log = open(LOG, "w", encoding="utf-8")
def p(*a):
    log.write(" ".join(str(x) for x in a) + "\n"); log.flush()

sys.path.insert(0, os.path.abspath("scripts"))
import tkinter as tk
import serve_dir_gui
import win_tray

td = os.path.join(os.environ["TEMP"], "sd_tray_pt")
os.makedirs(td, exist_ok=True)
open(os.path.join(td, "real.txt"), "wb").write(b"REAL")
p("stderr:", sys.stderr)

root = tk.Tk()
app = serve_dir_gui.ServeDirGUI(root)
app.dir_var.set(td); app.port_var.set("18112")
app.start_server()
time.sleep(0.8)
p("started, tray added:", app.tray.is_added() if app.tray else None)

app._on_window_close()                       # x= hide-to-tray
p("after close: state =", root.state())
try:
    with urllib.request.urlopen("http://127.0.0.1:18112/", timeout=3) as r:
        p("hidden but serving:", r.status)
except Exception as e:
    p("hidden GET failed:", e)

user32 = ctypes.windll.user32
user32.PostMessageW(app.tray.hwnd, win_tray.WM_TRAYICON, 0, win_tray.WM_LBUTTONUP)
dl = time.time() + 5
while time.time() < dl:
    root.update(); time.sleep(0.03)
p("after left click: state =", root.state())

app._quit_from_tray()
p("after quit: servers =", list(app.servers), "| tray =", app.tray)
try:
    root.update()
    p("root still alive (unexpected)")
except tk.TclError:
    p("root destroyed (expected)")
p("ALL-DONE")
