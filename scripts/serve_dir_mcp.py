#!/usr/bin/env python3
"""serve_dir_mcp.py — serve-dir 的零依赖 stdio MCP server（JSON-RPC 2.0 over stdio）。

把 serve-dir 的 CLI（scripts/serve_dir.py）以子进程方式暴露成 4 个 MCP 工具：
  serve_dir_serve / serve_dir_stop / serve_dir_list / serve_dir_status

协议（MCP stdio，最严格规则）：
  - 从 stdin 逐行读 JSON-RPC 2.0（一行一条 JSON），响应逐行写 stdout；
    **stdout 只走协议消息**，任何日志一律写 stderr。
  - 请求（带 id）必须响应；通知（无 id）不响应。
  - 非法 JSON 行 -> error -32700；未知方法（带 id）-> error -32601；
    工具执行出错不抛 JSON-RPC error，而是 result.isError=true + 说明文本。

生命周期：
  - stdin EOF -> 优雅退出（exit 0），**不杀掉已启动的服务器实例**
    （服务器本该比 MCP 会话活得久；用户用 serve_dir_stop 或自行结束）。
  - 实例登记表是进程内内存态，MCP 进程重启后为空；stop/list/status 只认
    当前会话内由本进程启动的实例。

子进程管理：
  serve_dir_serve 用 [sys.executable, scripts/serve_dir.py, --dir, …] 拉起 CLI；
  stderr 由 daemon 线程持续读入缓冲（防管道死锁）；启动后 3 秒内轮询
  「进程存活 + HTTP 200/302」判定成功；Windows 下子进程加 CREATE_NO_WINDOW。
"""

import datetime
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

SERVER_NAME = "serve-dir-mcp"
SERVER_VERSION = "1.0.0"
SERVE_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serve_dir.py")
CREATE_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW，仅 Windows 使用（调用处平台判断）

# 禁代理 opener：本机系统代理会劫持 localhost 请求（与 tests/ 回归同一模式）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 实例登记表：pid -> {pid, dir, port, allow_write, plain, started_at, url, _proc}
# 进程内内存态；工具在主线程顺序执行，无并发访问。
REGISTRY = {}


# ---------------------------------------------------------------- 工具实现

def _tool_ok(text):
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _tool_error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _log(msg):
    """日志只走 stderr，且写失败不得影响主流程。"""
    try:
        sys.stderr.write("[serve-dir-mcp] %s\n" % msg)
    except Exception:
        pass


def _probe(port, timeout):
    """HTTP 探测 http://127.0.0.1:<port>/（禁代理），返回状态码或 None。"""
    try:
        with _OPENER.open("http://127.0.0.1:%d/" % port, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def _spawn_serve(root, port, allow_write, plain):
    """拉起 serve_dir.py 子进程；返回 (Popen, stderr 累积缓冲 dict)。"""
    stderr_buf = {"text": ""}
    cmd = [sys.executable, SERVE_CLI, "--dir", root, "--port", str(port)]
    if plain:
        cmd.append("--plain")
    if allow_write:
        cmd.append("--allow-write")
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, **kwargs)

    def _drain():
        # 持续读子进程 stderr 到缓冲，防管道写满而死锁
        try:
            for line in proc.stderr:
                stderr_buf["text"] += line
        except Exception:
            pass

    threading.Thread(target=_drain, daemon=True, name="serve-stderr-drain").start()
    return proc, stderr_buf


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _tool_serve(args):
    root = args.get("dir")
    if not isinstance(root, str) or not root.strip():
        return _tool_error("dir 参数缺失（必填，字符串）")
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        return _tool_error("目录不存在: %s" % root)
    port = args.get("port", 8080)
    if not _is_int(port) or not (1 <= port <= 65535):
        return _tool_error("port 必须是 1-65535 的整数，得到: %r" % (port,))
    allow_write = bool(args.get("allow_write", False))
    plain = bool(args.get("plain", False))

    proc, stderr_buf = _spawn_serve(root, port, allow_write, plain)
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = [ln.strip() for ln in stderr_buf["text"].strip().splitlines()
                    if ln.strip()][-3:]
            detail = "；".join(tail) if tail else "（无 stderr 输出）"
            return _tool_error("serve_dir.py 启动失败（exit %s）：\n%s\n"
                               "常见原因：端口被占用/TIME_WAIT、目录不可读。"
                               % (proc.returncode, detail))
        code = _probe(port, 1.0)
        if code in (200, 302):
            rec = {
                "pid": proc.pid,
                "dir": root,
                "port": port,
                "allow_write": allow_write,
                "plain": plain,
                "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "url": "http://127.0.0.1:%d/" % port,
                "_proc": proc,
            }
            REGISTRY[proc.pid] = rec
            _log("serve pid=%d port=%d dir=%s" % (proc.pid, port, root))
            text = "serving %s at http://127.0.0.1:%d/ (pid=%d)" % (root, port, proc.pid)
            text += "\n写入：%s" % ("已开启（PUT 上传）" if allow_write else "只读")
            return _tool_ok(text)
        time.sleep(0.2)
    # 3 秒内进程存活但 HTTP 未就绪：视为启动失败，终止刚拉起的进程（避免不可管孤儿）
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        pass
    return _tool_error("启动后 3 秒内未通过 HTTP 自检（端口 %d），已终止刚拉起的进程。" % port)


def _tool_stop(args):
    pid = args.get("pid")
    port = args.get("port")
    if pid is None and port is None:
        return _tool_error("需要 pid 或 port（二选一或都给）")
    if pid is not None and not _is_int(pid):
        return _tool_error("pid 必须是整数，得到: %r" % (pid,))
    if port is not None and not _is_int(port):
        return _tool_error("port 必须是整数，得到: %r" % (port,))

    rec = None
    note = ""
    if pid is not None:
        rec = REGISTRY.get(pid)
    if rec is None and port is not None:
        matches = [r for r in REGISTRY.values() if r["port"] == port]
        if matches:
            rec = matches[0]  # port 冲突取第一个并说明
            if len(matches) > 1:
                note = "（同端口有 %d 个登记实例，已停止第一个）" % len(matches)
    if rec is None:
        return _tool_error("unknown instance: MCP 进程只跟踪当前会话内启动的实例")

    proc = rec["_proc"]
    proc.terminate()
    deadline = time.time() + 3.0
    while time.time() < deadline and proc.poll() is None:
        time.sleep(0.2)
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
    REGISTRY.pop(rec["pid"], None)
    _log("stop pid=%d port=%d" % (rec["pid"], rec["port"]))
    return _tool_ok("stopped pid=%d port=%d%s" % (rec["pid"], rec["port"], note))


def _tool_list(args):
    items = [{k: v for k, v in rec.items() if k != "_proc"}
             for rec in REGISTRY.values()]
    return _tool_ok(json.dumps(items, ensure_ascii=False, indent=2))


def _tool_status(args):
    pid = args.get("pid")
    port = args.get("port")
    if pid is not None and not _is_int(pid):
        return _tool_error("pid 必须是整数，得到: %r" % (pid,))
    if port is not None and not _is_int(port):
        return _tool_error("port 必须是整数，得到: %r" % (port,))

    out = []
    targets = []
    untracked_port = None
    if pid is not None:
        rec = REGISTRY.get(pid)
        if rec is not None:
            targets.append(rec)
        elif port is not None:
            untracked_port = port  # pid 未知但给了 port：按外部实例探测
    elif port is not None:
        matches = [r for r in REGISTRY.values() if r["port"] == port]
        if matches:
            targets = matches
        else:
            untracked_port = port  # 外部启动的服务器（不在登记表）
    else:
        targets = list(REGISTRY.values())

    if untracked_port is not None:
        code = _probe(untracked_port, 2.0)
        out.append({"port": untracked_port, "tracked": False,
                    "http_ok": code in (200, 302)})
    else:
        for rec in targets:
            alive = rec["_proc"].poll() is None
            code = _probe(rec["port"], 2.0)
            out.append({"pid": rec["pid"], "port": rec["port"],
                        "alive": alive, "http_ok": code in (200, 302),
                        "dir": rec["dir"], "url": rec["url"]})
    return _tool_ok(json.dumps(out, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- 工具表

TOOLS = [
    {
        "name": "serve_dir_serve",
        "description": "把一个目录开成局域网 HTTP 文件服务器（后台子进程；服务器寿命独立于本 MCP 会话）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dir": {"type": "string",
                        "description": "要分享的目录（绝对或相对路径）"},
                "port": {"type": "integer", "default": 8080,
                         "description": "监听端口，默认 8080"},
                "allow_write": {"type": "boolean", "default": False,
                                "description": "允许 PUT 上传（默认只读）"},
                "plain": {"type": "boolean", "default": False,
                          "description": "朴素列表（默认样式化）"},
            },
            "required": ["dir"],
        },
    },
    {
        "name": "serve_dir_stop",
        "description": "停止一个由本 MCP 会话启动的服务器实例（按 pid 或 port）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {"type": "integer",
                        "description": "实例 pid（与 port 二选一或都给）"},
                "port": {"type": "integer",
                         "description": "实例端口（与 pid 二选一或都给）"},
            },
        },
    },
    {
        "name": "serve_dir_list",
        "description": "列出当前 MCP 会话内启动的全部服务器实例",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "serve_dir_status",
        "description": "查询实例/端口的运行状态（进程存活 + HTTP 探测）；未登记的端口按外部实例探测",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {"type": "integer"},
                "port": {"type": "integer"},
            },
        },
    },
]

TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}
_TOOL_FNS = {
    "serve_dir_serve": _tool_serve,
    "serve_dir_stop": _tool_stop,
    "serve_dir_list": _tool_list,
    "serve_dir_status": _tool_status,
}


# ---------------------------------------------------------------- 协议层

def _emit(obj):
    """协议消息只走 stdout，逐行并 flush。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _reply(req_id, result):
    _emit({"jsonrpc": "2.0", "id": req_id, "result": result})


def _reply_error(req_id, code, message):
    _emit({"jsonrpc": "2.0", "id": req_id,
           "error": {"code": code, "message": message}})


def _handle_tools_call(req_id, params):
    params = params if isinstance(params, dict) else {}
    name = params.get("name")
    args = params.get("arguments")
    args = args if isinstance(args, dict) else {}
    fn = _TOOL_FNS.get(name) if isinstance(name, str) else None
    if fn is None:
        result = _tool_error("unknown tool: %r" % (name,))
    else:
        try:
            result = fn(args)
        except Exception as exc:  # 工具内部意外异常：不炸协议，转 isError
            _log("tool %s crashed: %s: %s" % (name, type(exc).__name__, exc))
            result = _tool_error("tool crashed: %s: %s" % (type(exc).__name__, exc))
    _reply(req_id, result)


def main():
    # stdio 统一 UTF-8（Windows 管道默认 locale 编码，中文会炸）
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _log("ready (pid=%d, tools=%d)" % (os.getpid(), len(TOOLS)))

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue  # 空行忽略
        try:
            msg = json.loads(line)
        except ValueError:
            _reply_error(None, -32700, "Parse error")
            continue
        if not isinstance(msg, dict):
            _reply_error(None, -32700, "Parse error")
            continue
        method = msg.get("method")
        if method is None:
            continue  # 非请求消息（如客户端返回的 response），忽略
        req_id = msg.get("id")
        is_notification = "id" not in msg

        if method == "initialize":
            if is_notification:
                continue
            params = msg.get("params")
            params = params if isinstance(params, dict) else {}
            pv = params.get("protocolVersion")
            _reply(req_id, {
                "protocolVersion": pv if pv else "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        elif method in ("notifications/initialized", "ping"):
            # initialized 是通知；ping 带 id 才回（ping 无 id 视为通知不响应）
            if not is_notification:
                _reply(req_id, {})
        elif method == "tools/list":
            if not is_notification:
                _reply(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            if is_notification:
                continue
            _handle_tools_call(req_id, msg.get("params"))
        else:
            if not is_notification:
                _reply_error(req_id, -32601, "Method not found")
    # stdin EOF：优雅退出（exit 0），不杀已启动的服务器实例
    _log("stdin EOF, exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
