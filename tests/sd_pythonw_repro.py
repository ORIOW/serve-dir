import os, sys, tempfile, time, urllib.request

LOG = os.path.join(tempfile.gettempdir(), "sd_pythonw_repro.log")
log = open(LOG, "w", encoding="utf-8")
def p(*a):
    log.write(" ".join(str(x) for x in a) + "\n"); log.flush()

p("pythonw sys.stderr =", sys.stderr)

sys.path.insert(0, os.path.abspath("scripts"))
import tkinter as tk
import serve_dir_gui

td = os.path.join(tempfile.gettempdir(), "sd_review_test", "plaindir")
if not os.path.isdir(td):
    td = os.getcwd()
p("serve dir:", td, "exists:", os.path.isdir(td))

root = tk.Tk(); root.withdraw()
app = serve_dir_gui.ServeDirGUI(root)
app._report_error = lambda t, m: p("ERROR-POPUP:", t, str(m)[:150])
app._report_info = lambda t, m: p("INFO-POPUP:", t, str(m)[:150])

app.dir_var.set(td); app.port_var.set("18110"); app.pretty_var.set(True)
app.start_server()
p("registered:", 18110 in app.servers)

state = None
dl = time.time() + 6
while time.time() < dl:
    root.update(); time.sleep(0.05)
    if 18110 in app.servers:
        v = app.tree.item("18110")["values"]
        if v[4] != "\u81ea\u68c0\u4e2d\u2026":
            state = v[4]; break
p("row state:", state)

for i in range(2):
    try:
        with urllib.request.urlopen("http://127.0.0.1:18110/", timeout=3) as r:
            p("GET", i, "status:", r.status, "len:", len(r.read()))
    except Exception as e:
        p("GET", i, "failed:", type(e).__name__, e)

app.stop_all(); root.destroy()
p("done")
