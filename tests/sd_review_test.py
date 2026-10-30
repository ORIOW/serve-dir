import io, os, shutil, socket, subprocess, sys, time, zipfile, urllib.request, urllib.error, urllib.parse, msvcrt

SCRIPTS = os.path.abspath('scripts')
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

# H1 human_size ??
sys.path.insert(0, SCRIPTS)
import serve_dir
check('H1 human_size 1MiB', serve_dir.human_size(1024 * 1024) == '1MB', serve_dir.human_size(1024 * 1024))
check('H1 human_size 1MiB-1', serve_dir.human_size(1024 * 1024 - 1) == '1023KB', serve_dir.human_size(1024 * 1024 - 1))

import tkinter as tk
import serve_dir_gui

errors = []
root = tk.Tk(); root.withdraw()
app = serve_dir_gui.ServeDirGUI(root)
app._report_error = lambda t, m: errors.append(('err', t, str(m)))
app._report_info = lambda t, m: errors.append(('info', t, str(m)))

base = os.path.join(os.environ['TEMP'], 'sd_review_test')
shutil.rmtree(base, ignore_errors=True)
td = os.path.join(base, 'plaindir'); os.makedirs(td)
open(os.path.join(td, 'one.txt'), 'wb').write(b'one')
pct = 'pct%20name.txt'
open(os.path.join(td, pct), 'wb').write(b'PCT')
os.makedirs(os.path.join(td, '__download'))
open(os.path.join(td, '__download', 'inner.txt'), 'wb').write(b'INNER')

def pump(sec=1.2):
    dl = time.time() + sec
    while time.time() < dl:
        root.update()
        time.sleep(0.05)

# P1 plain ?????P0 ?????
app.dir_var.set(td); app.port_var.set('18101'); app.pretty_var.set(False)
app.start_server()
check('P1 plain instance registered', 18101 in app.servers)
pump(1.5)
vals = app.tree.item('18101')['values']
check('P1 plain verify passed (row=???)', vals[4] == '运行中', vals)
check('P1 no ???? popup', not any(e[1] == '启动失败' for e in errors), errors)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:18101/', timeout=4) as r:
    hdr = r.headers.get('Server', ''); body = r.read()
check('P1 Server header', 'ServeDir/2.0' in hdr, hdr)
check('P1 native listing (no data-count, has file)', b'one.txt' in body and b'data-count' not in body, body[:120])
check('P1 plain PUT 403', http('PUT', 'http://127.0.0.1:18101/x.txt', b'x')[0] == 403)
app.tree.selection_set('18101'); app.toggle_write()
check('P1 plain toggle PUT 201', http('PUT', 'http://127.0.0.1:18101/x.txt', b'x')[0] == 201)
app.toggle_write()

# P2 ?? %20 ???????????
app.dir_var.set(td); app.port_var.set('18102'); app.pretty_var.set(True)
app.start_server(); pump(1.2)
st, body = http('GET', 'http://127.0.0.1:18102/__download?names=' + urllib.parse.quote(pct))
zf = zipfile.ZipFile(io.BytesIO(body))
check('P2 literal-percent name', st == 200 and pct in zf.namelist() and zf.read(pct) == b'PCT', (st, zf.namelist() if st == 200 else body[:80]))

# P5 __download ????? + ?????
st, body = http('GET', 'http://127.0.0.1:18102/__download/')
check('P5 __download/ browsable', st == 200 and b'inner.txt' in body, (st,))
st, body = http('GET', 'http://127.0.0.1:18102/__download?names=' + urllib.parse.quote('__download/inner.txt'))
zf = zipfile.ZipFile(io.BytesIO(body))
check('P5 endpoint intact', st == 200 and zf.read('__download/inner.txt') == b'INNER', (st,))

# P4 keep-alive?PUT ???????????????????
s = socket.create_connection(('127.0.0.1', 18102), 3)
s.sendall(b'PUT /..%2Foutside.txt HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\nConnection: keep-alive\r\n\r\nhello')
time.sleep(0.3)
buf = s.recv(4096)
check('P4 403 received', b'403' in buf.split(b'\r\n')[0], buf[:60])
s.settimeout(2)
closed = False
try:
    s.sendall(b'GET / HTTP/1.1\r\nHost: x\r\n\r\n')
    data = s.recv(4096)
    closed = (data == b'')
except OSError:
    closed = True  # ?????? = ???????????????
s.close()
check('P4 conn closed after reject', closed)

# P3 ?????????? CLI ????msvcrt ???????????
td3 = os.path.join(base, 'lockdir'); os.makedirs(td3)
open(os.path.join(td3, 'good.txt'), 'wb').write(b'GOOD')
locked = os.path.join(td3, 'locked.txt')
open(locked, 'wb').write(b'L' * 1024)
fd = os.open(locked, os.O_RDWR)
msvcrt.locking(fd, msvcrt.LK_NBLCK, 1024)
proc = subprocess.Popen([sys.executable, os.path.join(SCRIPTS, 'serve_dir.py'), '--dir', td3,
                         '--port', '18104', '--host', '127.0.0.1'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
dl = time.time() + 8
while time.time() < dl:
    try:
        socket.create_connection(('127.0.0.1', 18104), 0.3).close(); break
    except OSError:
        time.sleep(0.15)
st, body = http('GET', 'http://127.0.0.1:18104/__download?names=locked.txt&names=good.txt')
okzip = False
detail = st
if st == 200:
    zf = zipfile.ZipFile(io.BytesIO(body))
    nl = zf.namelist()
    okzip = ('good.txt' in nl and zf.read('good.txt') == b'GOOD'
             and '_serve-dir-skipped.txt' in nl and b'locked.txt' in zf.read('_serve-dir-skipped.txt'))
    detail = nl
check('P3 locked file skipped with manifest', okzip, detail)
proc.terminate(); proc.wait(timeout=5)
msvcrt.locking(fd, msvcrt.LK_UNLCK, 1024); os.close(fd)

app.stop_all()
app._quit_from_tray()
print('---')
print('FAILED:', fails if fails else 'none')
sys.exit(1 if fails else 0)



