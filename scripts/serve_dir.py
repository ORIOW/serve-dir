#!/usr/bin/env python3
# serve_dir.py — 固定位置的目录文件服务器（样式化列表 / 多选打包下载 / 可选 PUT 上传）
# 用法:
#   python serve_dir.py --dir <目录> [--port 8080] [--host 0.0.0.0] [--plain] [--allow-write]
# 设计:
#   脚本只此一份，放在 skills/serve-dir/scripts/；要分享哪个目录就传 --dir，
#   无需在每个目标目录里复制脚本。GUI 版（serve_dir_gui.py / .pyw）复用本模块。
# 安全:
#   - 上传仅当 allow_write 开启（CLI --allow-write / GUI 勾选），否则 PUT 一律 403；
#   - 所有客户端路径经 realpath + commonpath 校验，越界（../、盘符、绝对路径）一律拒绝；
#   - 单次上传上限 MAX_UPLOAD_BYTES，超限 413；
#   - Windows 上服务器套接字设置 SO_EXCLUSIVEADDRUSE，杜绝双绑定抢流量。

import argparse
import html
import json
import os
import posixpath
import socket
import socketserver
import string
import sys
import urllib.parse
import zipfile
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler

MAX_UPLOAD_BYTES = 1 * 1024 * 1024 * 1024  # 单次 PUT 上限 1 GiB


def get_lan_ip():
    """返回本机局域网 IPv4，用于打印可被其它机器访问的地址。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 不真正发包，只是借路由表拿到出口网卡 IP
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def human_size(size):
    if size >= 1024 * 1024:
        return f"{size // (1024 * 1024)}MB"
    if size >= 1024:
        return f"{size // 1024}KB"
    return f"{size}B"


PRETTY_PAGE = string.Template("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>serve-dir · $heading</title>
<style>
  body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; max-width: 760px; margin: 40px auto; padding: 0 20px; background: #fafafa; color: #1a1d21; }
  h2 { font-size: 20px; color: #333; }
  .bar { display: flex; align-items: center; gap: 12px; margin: 12px 0 14px; }
  .bar label { font-size: 13px; color: #555; display: flex; align-items: center; gap: 4px; }
  .bar button { font-size: 13px; padding: 5px 14px; border: 1px solid #0e6b6b; background: #0e6b6b; color: #fff; border-radius: 4px; cursor: pointer; }
  .bar button:disabled { border-color: #b8bfc6; background: #cfd4d9; cursor: not-allowed; }
  .row { display: flex; align-items: center; gap: 10px; padding: 8px 12px; margin: 4px 0; background: #fff; border: 1px solid #eceff1; border-radius: 6px; }
  .row:hover { border-color: #d0d7de; }
  .row input { margin: 0; }
  .row a.name { flex: 1 1 auto; word-break: break-all; text-decoration: none; color: #2563eb; }
  .row a.name:hover { text-decoration: underline; }
  .row .size { color: #9aa1a8; font-size: 12px; white-space: nowrap; }
  .info { color: #8a9199; font-size: 12.5px; margin: 14px 0 8px; }
</style>
</head>
<body>
<h2>📁 $heading</h2>
<p class="info" data-count="$count">$count 个条目 · 写入$write_state · <a href="/">返回根目录</a></p>
<div class="bar">
  <label><input type="checkbox" id="sel-all"> 全选</label>
  <button id="dl" disabled>下载所选（0）</button>
</div>
<div id="listing">
$rows
</div>
<script>
(function () {
  var boxes = Array.prototype.slice.call(document.querySelectorAll('input.pick'));
  var all = document.getElementById('sel-all');
  var btn = document.getElementById('dl');
  function picked() { return boxes.filter(function (b) { return b.checked; }); }
  function refresh() {
    var n = picked().length;
    btn.disabled = (n === 0);
    btn.textContent = '下载所选（' + n + '）';
  }
  all.addEventListener('change', function () {
    boxes.forEach(function (b) { b.checked = all.checked; });
    refresh();
  });
  boxes.forEach(function (b) { b.addEventListener('change', refresh); });
  btn.addEventListener('click', function () {
    var qs = picked().map(function (b) { return 'names=' + encodeURIComponent(b.value); }).join('&');
    window.location.href = '/__download?' + qs;
  });
  refresh();
})();
</script>
</body></html>""")


class ServeDirHandler(SimpleHTTPRequestHandler):
    """共用基类：在标准静态服务之上加两个能力——
    1) GET /__download?names=... 多选打包 zip（pretty/plain 两种列表都可用）；
    2) PUT 上传，仅当 server.allow_write 为真，否则 403。
    """

    server_version = "ServeDir/2.0"

    def log_message(self, fmt, *args):
        """pythonw（双击）下 sys.stderr 为 None，默认实现会抛 AttributeError
        杀死请求线程（连接无响应关闭）——这里安全化：无 stderr 就跳过。"""
        stderr = getattr(sys, "stderr", None)
        if stderr is not None:
            try:
                sys.stderr.write("%s - - [%s] %s\n" %
                                 (self.address_string(), self.log_date_time_string(), fmt % args))
            except OSError:
                pass  # 控制台被关闭等场景

    def log_error(self, fmt, *args):
        """同 log_message：错误日志也做 stderr 安全化。"""
        self.log_message(fmt, *args)

    # ---------- 通用工具 ----------

    def _local_abspath(self, url_path, decoded=False):
        """把客户端路径映射到 serve_root 内的绝对路径；越界返回 None。

        decoded=False：url_path 是原始 URL 路径（含百分号编码），先解码一次；
        decoded=True：调用方传入的已是解码后相对路径（如 parse_qs 结果），
        不再二次解码——否则文件名含字面 "%20" 类序列时会被错误转换。
        """
        root = os.path.realpath(self.server.serve_root)
        if decoded:
            rel = url_path.lstrip("/")
        else:
            rel = urllib.parse.unquote(urllib.parse.urlsplit(url_path).path).lstrip("/")
        if not rel:
            return root
        candidate = os.path.realpath(os.path.join(root, rel))
        try:
            inside = os.path.commonpath([candidate, root]) == root
        except ValueError:  # 跨盘符
            inside = False
        return candidate if inside else None

    def _send_json(self, code, obj):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ---------- 多选打包下载 ----------

    def do_GET(self):
        # 精确匹配根级 /__download 为打包端点；名为 __download 的目录仍可用 /__download/ 浏览
        if urllib.parse.urlsplit(self.path).path == "/__download":
            return self._send_zip()
        super().do_GET()

    def _zip_add(self, zf, path, root, skipped):
        """把 path（文件或目录）加入 zip；目录递归。arcname 相对 serve_root。

        单个条目读取失败（被占用/无权限等）只跳过该条目，不影响整包；
        跳过清单记入 skipped，最终写入 zip 内的 _serve-dir-skipped.txt。
        """
        if os.path.isfile(path):
            try:
                zf.write(path, os.path.relpath(path, root).replace(os.sep, "/"))
            except OSError:
                skipped.append(os.path.relpath(path, root).replace(os.sep, "/"))
        elif os.path.isdir(path):
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames.sort()
                filenames.sort()
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    try:
                        zf.write(full, os.path.relpath(full, root).replace(os.sep, "/"))
                    except OSError:
                        skipped.append(os.path.relpath(full, root).replace(os.sep, "/"))

    def _send_zip(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        names = list(dict.fromkeys(n for n in qs.get("names", []) if n))
        root = os.path.realpath(self.server.serve_root)
        resolved = []
        for name in names:
            p = self._local_abspath(name, decoded=True)
            if p and p != root and os.path.exists(p):
                resolved.append(p)
        if not resolved:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "没有可下载的有效条目"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", 'attachment; filename="serve-dir-download.zip"')
        self.end_headers()
        self.close_connection = True
        skipped = []
        try:
            # wfile 不可 seek，zipfile 自动走流式模式（data descriptor）
            with zipfile.ZipFile(self.wfile, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in resolved:
                    self._zip_add(zf, p, root, skipped)
                if skipped:
                    zf.writestr("_serve-dir-skipped.txt",
                                "以下条目读取失败，已跳过：\n" + "\n".join(skipped) + "\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------- 可选写入（PUT 上传） ----------

    def do_PUT(self):
        if not getattr(self.server, "allow_write", False):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "写入未开启：服务器当前为只读"})
            return
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            self.close_connection = True  # 不消费请求体，直接关闭连接防残留体误解析
            self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "不支持 chunked 上传，请携带 Content-Length"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Content-Length 非法"})
            return
        if length <= 0:
            self.close_connection = True
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "缺少请求体"})
            return
        if length > MAX_UPLOAD_BYTES:
            self.close_connection = True  # 不读取超大请求体
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                            {"error": f"超过单次上传上限 {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB"})
            return
        target = self._local_abspath(self.path)
        if target is None:
            self.close_connection = True
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "路径越界：拒绝写入服务目录之外"})
            return
        if os.path.isdir(target):
            self.close_connection = True
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "目标是一个已存在的目录"})
            return
        parent = os.path.dirname(target)
        try:
            os.makedirs(parent, exist_ok=True)
            remaining = length
            with open(target, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        except OSError as e:
            self.close_connection = True
            self.log_error("PUT write failed: %s", e)
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "写入失败：文件可能被其他程序占用或无写入权限"})
            return
        if remaining:  # 客户端承诺的长度没发够，丢弃半成品
            try:
                os.remove(target)
            except OSError:
                pass
            self.close_connection = True
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "请求体不完整"})
            return
        rel = os.path.relpath(target, os.path.realpath(self.server.serve_root)).replace(os.sep, "/")
        self._send_json(HTTPStatus.CREATED, {"ok": True, "path": rel, "size": length})


class PrettyHandler(ServeDirHandler):
    """样式化列表页：多选下载 UI + 子目录导航。"""

    def list_directory(self, path):
        try:
            names = sorted(os.listdir(path))
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "No permission to list directory")
            return None

        root = os.path.realpath(self.server.serve_root)
        rows = []
        cur = urllib.parse.urlsplit(self.path).path
        if os.path.realpath(path) != root:  # 子目录：给返回上级链接
            parent = urllib.parse.quote(posixpath.dirname(cur.rstrip("/")) or "/", safe="/%")
            rows.append(f'<div class="row"><a class="name" href="{parent}">⬆ .. 上级目录</a></div>')
        for name in names:
            full = os.path.join(path, name)
            is_dir = os.path.isdir(full)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if is_dir:
                rel += "/"
            href = urllib.parse.quote(rel, safe="/")
            display = html.escape(name + ("/" if is_dir else ""))
            try:
                size_str = "" if is_dir else human_size(os.path.getsize(full))
            except OSError:
                size_str = ""
            icon = "📁" if is_dir else "📄"
            rows.append(
                f'<div class="row">'
                f'<input type="checkbox" class="pick" value="{html.escape(rel, quote=True)}">'
                f'<a class="name" href="{href}">{icon} {display}</a>'
                f'<span class="size">{size_str}</span>'
                f'</div>'
            )

        rel_root = os.path.relpath(path, root)
        heading = html.escape("文件列表" + ("" if rel_root == "." else f" · {rel_root}"))
        write_state = "已开启" if getattr(self.server, "allow_write", False) else "未开启（只读）"
        page = PRETTY_PAGE.substitute(
            heading=heading,
            count=len(names),
            rows="\n".join(rows),
            write_state=write_state,
        )
        payload = page.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        return None


class PlainHandler(ServeDirHandler):
    """朴素原生列表（无样式）。多选下载端点与 PUT 依然可用。"""


class ThreadingHTTPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # Windows: 不再使用 SO_REUSEADDR（它允许双绑定抢流量），改用独占绑定。
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # 仅 Windows 生效
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        """pythonw 下 sys.stderr 为 None，socketserver 默认打印会二次抛错。"""
        if getattr(sys, "stderr", None) is not None:
            super().handle_error(request, client_address)


def main():
    parser = argparse.ArgumentParser(description="固定位置的目录文件服务器")
    parser.add_argument("--dir", required=True, help="要分享的目录（绝对或相对路径）")
    parser.add_argument("--port", type=int, default=8080, help="监听端口，默认 8080")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址，默认 0.0.0.0（所有接口）")
    parser.add_argument("--plain", action="store_true", help="使用朴素列表而非样式化列表")
    parser.add_argument("--allow-write", action="store_true",
                        help="允许 client 通过 PUT 上传（默认只读；上传上限 1 GiB）")
    args = parser.parse_args()

    root = os.path.abspath(os.path.expanduser(args.dir))
    if not os.path.isdir(root):
        print(f"[serve_dir] 错误: 目录不存在: {root}", file=sys.stderr, flush=True)
        sys.exit(1)

    handler = PlainHandler if args.plain else PrettyHandler
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), partial(handler, directory=root))
    except OSError as e:
        print(f"[serve_dir] 错误: 端口 {args.port} 绑定失败: {e}\n"
              f"           可能已被占用，或处于系统释放等待期（TIME_WAIT）；请稍候重试或更换端口。",
              file=sys.stderr, flush=True)
        sys.exit(1)
    httpd.serve_root = root
    httpd.allow_write = bool(args.allow_write)

    lan = get_lan_ip()
    print(f"[serve_dir] 服务于: {root}", flush=True)
    print(f"[serve_dir] 本机:   http://127.0.0.1:{args.port}/", flush=True)
    print(f"[serve_dir] 局域网: http://{lan}:{args.port}/", flush=True)
    print(f"[serve_dir] 写入:   {'已开启（PUT 上传，上限 1 GiB）' if args.allow_write else '未开启（只读）'}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve_dir] 已停止", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
