import io, os, shutil, socket, subprocess, sys, time, zipfile, urllib.request, urllib.error, urllib.parse

PY = os.environ.get("SERVE_DIR_PY", sys.executable or "python3")
SCRIPTS = os.path.abspath('scripts')
SERVE = os.path.join(SCRIPTS, 'serve_dir.py')

CN = '\u4e2d\u6587 \u6587\u4ef6.txt'   # "?? ??.txt"
CN_URL = '/%E4%B8%AD%E6%96%87%20%E6%96%87%E4%BB%B6.txt'

td = os.path.join(os.environ['TEMP'], 'sd_fix_test')
shutil.rmtree(td, ignore_errors=True)
os.makedirs(os.path.join(td, 'sub'), exist_ok=True)
open(os.path.join(td, 'one.txt'), 'wb').write(b'one-content')
open(os.path.join(td, CN), 'wb').write('\u4e2d\u6587\u5185\u5bb9'.encode('utf-8'))
open(os.path.join(td, 'sub', 'inner.txt'), 'wb').write(b'inner-content')
outside = os.path.join(os.path.dirname(td), 'sd_fix_outside.txt')
open(outside, 'wb').write(b'OUTSIDE-SECRET')
print('test dir:', td)

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

def start_cli(port, extra):
    return subprocess.Popen([PY, SERVE, '--dir', td, '--port', str(port), '--host', '127.0.0.1'] + extra,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def wait_up(port, timeout=8):
    dl = time.time() + timeout
    while time.time() < dl:
        try:
            socket.create_connection(('127.0.0.1', port), 0.3).close(); return True
        except OSError:
            time.sleep(0.15)
    return False

BASE = 'http://127.0.0.1:18090'

# ---------- T2a read-only ----------
p = start_cli(18090, [])
try:
    check('T2a server up', wait_up(18090))
    st, body = http('GET', BASE + '/')
    check('T2a GET / 200', st == 200, st)
    check('T2a count marker', b'data-count="3"' in body, body[:200])
    check('T2a checkbox + button', b'class="pick"' in body and '\u4e0b\u8f7d\u6240\u9009'.encode('utf-8') in body)
    st, body = http('GET', BASE + '/one.txt'); check('T2a GET file', st == 200 and body == b'one-content', st)
    st, body = http('GET', BASE + CN_URL); check('T2a GET chinese+space', st == 200 and body == '\u4e2d\u6587\u5185\u5bb9'.encode('utf-8'), st)
    st, body = http('PUT', BASE + '/up.txt', b'x'); check('T2a PUT blocked 403', st == 403, (st, body[:80]))
    st, body = http('PUT', BASE + '/..%2Fsd_fix_outside.txt', b'x'); check('T2a PUT escape 403', st == 403, (st, body[:80]))
    st, body = http('GET', BASE + '/__download?names=one.txt&names=' + urllib.parse.quote(CN))
    check('T2a zip 200', st == 200, st)
    zf = zipfile.ZipFile(io.BytesIO(body)); nl = zf.namelist()
    check('T2a zip entries', 'one.txt' in nl and CN in nl, nl)
    check('T2a zip contents', zf.read('one.txt') == b'one-content' and zf.read(CN) == '\u4e2d\u6587\u5185\u5bb9'.encode('utf-8'))
    st, body = http('GET', BASE + '/__download?names=sub')
    zf = zipfile.ZipFile(io.BytesIO(body))
    check('T2a zip dir recursive', st == 200 and 'sub/inner.txt' in zf.namelist(), (st, zf.namelist() if st == 200 else body[:80]))
    st, body = http('GET', BASE + '/__download?names=..%2Fsd_fix_outside.txt')
    check('T2a zip escape rejected', st == 404, (st, body[:80]))
    st, body = http('POST', BASE + '/', b'x'); check('T2a POST 501', st == 501, st)
finally:
    p.terminate(); p.wait(timeout=5); time.sleep(0.3)

# ---------- T2b write-enabled ----------
p = start_cli(18091, ['--allow-write'])
try:
    check('T2b server up', wait_up(18091))
    st, body = http('PUT', 'http://127.0.0.1:18091/up.txt', b'hello')
    check('T2b PUT 201', st == 201, (st, body[:100]))
    check('T2b file on disk', open(os.path.join(td, 'up.txt'), 'rb').read() == b'hello')
    st, body = http('PUT', 'http://127.0.0.1:18091/newdir/nested.txt', b'nnn')
    check('T2b nested PUT 201', st == 201, (st, body[:100]))
    check('T2b nested on disk', open(os.path.join(td, 'newdir', 'nested.txt'), 'rb').read() == b'nnn')
    st, body = http('PUT', 'http://127.0.0.1:18091/..%2Fsd_fix_outside.txt', b'hack')
    check('T2b escape 403', st == 403, (st, body[:100]))
    check('T2b outside untouched', open(outside, 'rb').read() == b'OUTSIDE-SECRET')
    st, body = http('PUT', 'http://127.0.0.1:18091/one.txt', b'overwrite')
    check('T2b overwrite 201', st == 201 and open(os.path.join(td, 'one.txt'), 'rb').read() == b'overwrite', st)
finally:
    p.terminate(); p.wait(timeout=5); time.sleep(0.3)

# ---------- T2c oversize 413 ----------
p = start_cli(18091, ['--allow-write'])
try:
    wait_up(18091)
    s = socket.create_connection(('127.0.0.1', 18091), 3)
    s.sendall(b'PUT /big.bin HTTP/1.0\r\nHost: x\r\nContent-Length: 2000000000\r\n\r\n')
    resp = s.recv(300); s.close()
    check('T2c 413 oversize', b'413' in resp, resp[:60])
finally:
    p.terminate(); p.wait(timeout=5); time.sleep(0.3)

# ---------- T3 GUI headless ----------
sys.path.insert(0, SCRIPTS)
import tkinter as tk
import serve_dir_gui

errors = []
root = tk.Tk(); root.withdraw()
app = serve_dir_gui.ServeDirGUI(root)
app._report_error = lambda t, m: errors.append(('err', t, str(m)))
app._report_info = lambda t, m: errors.append(('info', t, str(m)))

app.dir_var.set(td)
app.port_var.set('18093')
app.start_server()
check('T3 GUI running', 18093 in app.servers, errors)
st, body = http('GET', 'http://127.0.0.1:18093/')
check('T3 marker count=5', st == 200 and b'data-count="5"' in body, (st, body[:120]))
st, body = http('PUT', 'http://127.0.0.1:18093/via_gui.txt', b'g1')
check('T3 write-off PUT 403', st == 403, st)
app.tree.selection_set('18093'); app.toggle_write()
st, body = http('PUT', 'http://127.0.0.1:18093/via_gui.txt', b'g1')
check('T3 hot-toggle PUT 201', st == 201, (st, body[:80]))
app.tree.selection_set('18093'); app.toggle_write()
st, body = http('PUT', 'http://127.0.0.1:18093/via_gui2.txt', b'g2')
check('T3 hot-toggle back 403', st == 403, st)
app.tree.selection_set('18093'); app.stop_selected()
check('T3 stopped clean', 18093 not in app.servers and len(app.servers) == 0)
try:
    s2 = socket.socket(); s2.bind(('127.0.0.1', 18093)); s2.close()
    check('T3 port freed after stop', True)
except OSError as e:
    check('T3 port freed after stop', False, e)

app.port_var.set('abc'); app.start_server()
check('T3 invalid port blocked', any(e[0] == 'err' and e[1] == '\u7aef\u53e3\u65e0\u6548' for e in errors), errors[-3:])
errors.clear(); app.port_var.set('70000'); app.start_server()
check('T3 range blocked', any(e[0] == 'err' and e[1] == '\u7aef\u53e3\u65e0\u6548' for e in errors))
errors.clear(); app.port_var.set('18095'); app.dir_var.set(r'C:\sd_fix_no_such_dir'); app.start_server()
check('T3 missing dir blocked', any(e[0] == 'err' and e[1] == '\u76ee\u5f55\u4e0d\u5b58\u5728' for e in errors), errors[-3:])
errors.clear(); app.dir_var.set(td); app.port_var.set('18094')
blocker = socket.socket(); blocker.bind(('127.0.0.1', 18094)); blocker.listen(1)
app.start_server()
check('T3 occupied blocked', any(e[0] == 'err' and e[1] == '\u7aef\u53e3\u88ab\u5360\u7528' for e in errors) and len(app.servers) == 0, errors[-3:])
blocker.close()

app._quit_from_tray()
print('---')
print('FAILED:', fails if fails else 'none')
sys.exit(1 if fails else 0)



