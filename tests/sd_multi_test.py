import io, os, shutil, socket, subprocess, sys, time, zipfile, urllib.request, urllib.error, urllib.parse

SCRIPTS = os.path.abspath('scripts')

fails = []
def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else ' | ' + str(detail)))
    if not cond:
        fails.append(name)

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

# ---------- ???????? ----------
base = os.path.join(os.environ['TEMP'], 'sd_multi_test')
shutil.rmtree(base, ignore_errors=True)
td1 = os.path.join(base, 'dir1'); td2 = os.path.join(base, 'dir2')
os.makedirs(td1); os.makedirs(td2)
open(os.path.join(td1, 'fileA.txt'), 'wb').write(b'A-content')
open(os.path.join(td2, 'fileB.txt'), 'wb').write(b'B-content')
outside = os.path.join(base, 'outside.txt')
open(outside, 'wb').write(b'OUTSIDE')

# ---------- GUI ??????? ----------
sys.path.insert(0, SCRIPTS)
import tkinter as tk
import serve_dir_gui

opened_urls = []
class FakeWeb:
    @staticmethod
    def open(url, *a, **k): opened_urls.append(url)
serve_dir_gui.webbrowser = FakeWeb

errors = []
root = tk.Tk(); root.withdraw()
app = serve_dir_gui.ServeDirGUI(root)
app._report_error = lambda t, m: errors.append(('err', t, str(m)))
app._report_info = lambda t, m: errors.append(('info', t, str(m)))

def wait_up(port, timeout=8):
    dl = time.time() + timeout
    while time.time() < dl:
        try:
            socket.create_connection(('127.0.0.1', port), 0.3).close(); return True
        except OSError:
            time.sleep(0.15)
    return False

# M1: ?????????? + ??????
app.dir_var.set(td1); app.port_var.set('18091'); app.start_server()
app.dir_var.set(td2); app.port_var.set('18092'); app.start_server()
app.dir_var.set(td1); app.port_var.set('18093'); app.start_server()
check('M1 three instances running', len(app.servers) == 3 and app.tree.get_children() and len(app.tree.get_children()) == 3, len(app.servers))
st1, b1 = http('GET', 'http://127.0.0.1:18091/')
st2, b2 = http('GET', 'http://127.0.0.1:18092/')
st3, b3 = http('GET', 'http://127.0.0.1:18093/')
check('M1 dir1 served on 18091', st1 == 200 and b'data-count="1"' in b1 and b'fileA.txt' in b1, st1)
check('M1 dir2 served on 18092', st2 == 200 and b'data-count="1"' in b2 and b'fileB.txt' in b2, st2)
check('M1 dir1 served again on 18093', st3 == 200 and b'data-count="1"' in b3 and b'fileA.txt' in b3, st3)
check('M1 write defaults off', http('PUT', 'http://127.0.0.1:18091/new.txt', b'x')[0] == 403)

# M2: ?? 18091 ??????? web ?? URL?
app.tree.selection_set('18091')
app.open_browser()
check('M2 open_browser uses selected port', opened_urls == ['http://127.0.0.1:18091/'], opened_urls)

# M3: ??????? 18091?? PUT 201 ? ?? ? 403?????????
app.toggle_write()
st, body = http('PUT', 'http://127.0.0.1:18091/w.txt', b'W1')
check('M3 toggle-on PUT 201', st == 201 and open(os.path.join(td1, 'w.txt'), 'rb').read() == b'W1', (st, body[:80]))
check('M3 others unaffected', http('PUT', 'http://127.0.0.1:18092/w.txt', b'x')[0] == 403 and http('PUT', 'http://127.0.0.1:18093/w.txt', b'x')[0] == 403)
app.toggle_write()
check('M3 toggle-back 403', http('PUT', 'http://127.0.0.1:18091/w2.txt', b'x')[0] == 403)

# M4: ?? 18091 ?? ? ??????
app.tree.selection_set('18091')
app.stop_selected()
check('M4 stopped selected', 18091 not in app.servers and len(app.servers) == 2, list(app.servers))
check('M4 18091 down', http('GET', 'http://127.0.0.1:18091/')[0] is None)
st, body = http('GET', 'http://127.0.0.1:18092/')
check('M4 18092 still up', st == 200 and b'fileB.txt' in body, st)

# M5: ???? ? ??
app.dir_var.set(td1); app.port_var.set('18092'); app.start_server()
check('M5 duplicate port blocked', any(e[0] == 'err' and e[1] == '\u7aef\u53e3\u91cd\u590d' for e in errors) and len(app.servers) == 2, errors[-2:])
# M6: ???? ? ??
errors.clear(); app.port_var.set('18094')
blocker = socket.socket(); blocker.bind(('127.0.0.1', 18094)); blocker.listen(1)
app.start_server()
check('M6 occupied blocked', any(e[0] == 'err' and e[1] == '\u7aef\u53e3\u88ab\u5360\u7528' for e in errors) and len(app.servers) == 2, errors[-2:])
blocker.close()
# M7: ???? / ???? ? ??
errors.clear(); app.port_var.set('abc'); app.start_server()
check('M7 invalid port blocked', any(e[0] == 'err' and e[1] == '\u7aef\u53e3\u65e0\u6548' for e in errors))
errors.clear(); app.port_var.set('18095'); app.dir_var.set(r'C:\sd_multi_no_such'); app.start_server()
check('M7 missing dir blocked', any(e[0] == 'err' and e[1] == '\u76ee\u5f55\u4e0d\u5b58\u5728' for e in errors), errors[-2:])

# M8: ????????????18092 ?? zip ?? 18093?
app.dir_var.set(td2); app.port_var.set('18092')  # ???????????
app.tree.selection_set('18092'); app.toggle_write()
st, body = http('PUT', 'http://127.0.0.1:18092/up.txt', b'UP')
check('M8 PUT 201 on 18092', st == 201, (st, body[:80]))
app.tree.selection_set('18093'); app.toggle_write()
st, body = http('PUT', 'http://127.0.0.1:18093/up2.txt', b'UP2')
check('M8 PUT 201 on 18093', st == 201, (st, body[:80]))
st, body = http('GET', 'http://127.0.0.1:18093/__download?names=up2.txt')
zf = zipfile.ZipFile(io.BytesIO(body))
check('M8 zip from 18093', st == 200 and zf.read('up2.txt') == b'UP2', (st,))

# M9: ???? ? ??????
app.tree.selection_set('18092', '18093')
app.stop_all()
check('M9 all stopped', len(app.servers) == 0 and not app.tree.get_children())
# 跨平台"端口已释放"探测：connect 应被拒绝（= 无人监听）。
# 原裸 bind() 探测在 macOS/Linux 会被 TIME_WAIT 误判为 EADDRINUSE（Errno 48）；
# connect 不受 TIME_WAIT 影响。
freed = True
for p in (18091, 18092, 18093):
    c = socket.socket(); c.settimeout(2)
    try:
        c.connect(('127.0.0.1', p))
        freed = False; print('still listening', p)
    except OSError:
        pass
    finally:
        c.close()
check('M9 ports freed', freed)
check('M9 buttons disabled', str(app.stopall_btn['state']) == 'disabled')

app._quit_from_tray()
print('---')
print('FAILED:', fails if fails else 'none')
sys.exit(1 if fails else 0)

