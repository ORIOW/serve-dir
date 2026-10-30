#!/usr/bin/env python3
# serve_dir_gui.py — 带 GUI 的目录文件服务器（serve-dir 技能）
# 用法:
#   双击 serve_dir_gui.pyw          （推荐，无控制台）
#   pythonw serve_dir_gui.py        （无控制台）
#   python serve_dir_gui.py         （自动转交 pythonw；找不到 pythonw 时带控制台运行）
# 功能: 同时管理多个 server 实例——每个实例独立的目录/端口/写入开关/样式；
#       网页端多选打包下载；PUT 上传（默认只读）。
# 说明: 本模块不再自我重生——旧版在 main() 里用 sys.executable 转发自身导致无限进程链，
#       现改为「仅在 python.exe 下、且找得到 pythonw.exe 时」显式转交一次（带环境变量哨兵）。

import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.parse
import urllib.request
import webbrowser
from functools import partial
from tkinter import filedialog, messagebox, ttk

from serve_dir import PlainHandler, PrettyHandler, ThreadingHTTPServer, get_lan_ip

RELAUNCH_SENTINEL = "SERVE_DIR_GUI_RELAUNCHED"


class ServerThread(threading.Thread):
    """后台线程：只负责 serve_forever；绑定已在主线程同步完成，失败可即时感知。"""

    def __init__(self, httpd):
        super().__init__(daemon=True)
        self.httpd = httpd

    def run(self):
        try:
            self.httpd.serve_forever()
        except Exception:
            pass  # 进程退出时 daemon 线程被终止，属预期

    def stop(self):
        self.httpd.shutdown()


class ServerRecord:
    """一个运行中的 server 实例。"""

    __slots__ = ("httpd", "thread", "dir", "port", "write", "pretty")

    def __init__(self, httpd, thread, dirpath, port, write, pretty):
        self.httpd = httpd
        self.thread = thread
        self.dir = dirpath
        self.port = port
        self.write = write
        self.pretty = pretty


class ServeDirGUI:
    """serve-dir GUI 主窗口：支持同时运行多个 server（每条目一个端口/目录）。"""

    def __init__(self, root):
        self.root = root
        self.root.title("serve-dir — 局域网文件服务器（多实例）")
        self.root.geometry("700x540")
        self.root.minsize(620, 460)

        self.servers = {}  # port -> ServerRecord

        # 新实例的配置（点「启动新服务器」时生效）
        self.dir_var = tk.StringVar(value=os.getcwd())
        self.port_var = tk.StringVar(value="8080")
        self.allow_write_var = tk.BooleanVar(value=False)
        self.pretty_var = tk.BooleanVar(value=True)

        self._setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

    # ---------- UI ----------

    def _setup_ui(self):
        # 新服务器配置区
        cfg = ttk.LabelFrame(self.root, text="新服务器配置（点「启动新服务器」时生效）", padding=8)
        cfg.pack(fill="x", padx=10, pady=(8, 4))
        dir_entry = ttk.Entry(cfg, textvariable=self.dir_var, width=40)
        dir_entry.grid(row=0, column=0, sticky="we", padx=(0, 6))
        dir_entry.bind("<Return>", lambda e: self.apply_directory())
        ttk.Button(cfg, text="浏览…", command=self.browse_directory).grid(row=0, column=1, padx=(0, 10))
        ttk.Label(cfg, text="端口:").grid(row=0, column=2)
        ttk.Spinbox(cfg, from_=1, to=65535, width=7, textvariable=self.port_var).grid(row=0, column=3, padx=(4, 12))
        ttk.Checkbutton(cfg, text="允许写入", variable=self.allow_write_var).grid(row=0, column=4, padx=(0, 8))
        ttk.Checkbutton(cfg, text="pretty 样式", variable=self.pretty_var).grid(row=0, column=5)
        cfg.columnconfigure(0, weight=1)

        # 操作按钮行
        btn = ttk.Frame(self.root, padding=(10, 2))
        btn.pack(fill="x")
        self.start_btn = ttk.Button(btn, text="启动新服务器", command=self.start_server)
        self.start_btn.pack(side="left")
        self.open_btn = ttk.Button(btn, text="打开浏览器", command=self.open_browser, state="disabled")
        self.open_btn.pack(side="left", padx=6)
        self.write_btn = ttk.Button(btn, text="切换写入", command=self.toggle_write, state="disabled")
        self.write_btn.pack(side="left")
        self.stop_btn = ttk.Button(btn, text="停止选中", command=self.stop_selected, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.stopall_btn = ttk.Button(btn, text="停止全部", command=self.stop_all, state="disabled")
        self.stopall_btn.pack(side="left")

        # 运行列表
        lf = ttk.LabelFrame(self.root, text="运行中的服务器", padding=(8, 4))
        lf.pack(fill="both", expand=True, padx=10, pady=4)
        cols = ("port", "dir", "write", "pretty", "state")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended", height=8)
        for cid, text, width, anchor in (
            ("port", "端口", 60, "center"),
            ("dir", "目录", 340, "w"),
            ("write", "写入", 60, "center"),
            ("pretty", "样式", 70, "center"),
            ("state", "状态", 80, "center"),
        ):
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor=anchor)
        vsb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # 状态栏
        self.status_var = tk.StringVar(value="就绪 · 未运行任何服务器")
        ttk.Label(self.root, textvariable=self.status_var, foreground="gray",
                  padding=(12, 0, 12, 8)).pack(fill="x")

        # 主线程队列：自检结果 / 托盘回调都经这里投递回 UI 线程（worker 只入队，不碰 UI）
        self._main_queue = queue.Queue()
        self.root.after(60, self._poll_main_queue)

        # 系统托盘（Windows；加载失败自动降级为无托盘）
        self.tray = None
        self._tray_hint_shown = False
        self._setup_tray()

    # ---------- 可替换的弹窗出口（便于自动化测试） ----------

    def _report_error(self, title, msg):
        messagebox.showerror(title, msg)

    def _report_info(self, title, msg):
        messagebox.showinfo(title, msg)

    def _set_status(self, text):
        self.status_var.set(text)

    # ---------- 交互 ----------

    def browse_directory(self):
        directory = filedialog.askdirectory(title="选择要分享的目录")
        if directory:
            self.dir_var.set(os.path.normpath(directory))

    def apply_directory(self):
        """目录框回车：应用手输路径（有效则归一化，无效则提示）。"""
        d = self._current_dir()
        if os.path.isdir(d):
            self.dir_var.set(d)
        else:
            self._report_error("目录无效", f"目录不存在：\n{d}")

    # ---------- 校验 ----------

    def _current_dir(self):
        return os.path.abspath(os.path.expanduser(self.dir_var.get()))

    def _current_port(self):
        """返回 (port, None) 或 (None, 错误消息)。"""
        raw = self.port_var.get().strip()
        try:
            port = int(raw)
        except ValueError:
            return None, f"端口必须是数字，当前为：{raw!r}"
        if not (1 <= port <= 65535):
            return None, f"端口须在 1–65535 之间，当前为：{port}"
        return port, None

    def _port_occupied(self, port):
        """真实监听者才会接受连接（TIME_WAIT 不会误报）。"""
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    # ---------- 多实例管理 ----------

    @staticmethod
    def _write_text(write):
        return "开" if write else "只读"

    @staticmethod
    def _pretty_text(pretty):
        return "pretty" if pretty else "plain"

    def start_server(self):
        """按当前配置新增一个 server 实例（不影响已在运行的实例）。"""
        d = self._current_dir()
        if not os.path.isdir(d):
            self._report_error("目录不存在", f"目录不存在：\n{d}")
            return
        port, err = self._current_port()
        if err:
            self._report_error("端口无效", err)
            return
        if port in self.servers:
            self._report_error("端口重复", f"端口 {port} 已由本工具的一个服务器使用。\n每个服务器需要独立端口。")
            return
        if self._port_occupied(port):
            self._report_error("端口被占用",
                               f"端口 {port} 已有程序在监听。\n请更换端口，或停止占用该端口的程序后重试。")
            return

        handler = PrettyHandler if self.pretty_var.get() else PlainHandler
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", port), partial(handler, directory=d))
        except OSError as e:
            self._report_error("绑定失败",
                               f"端口 {port} 绑定失败：{e}\n"
                               "可能处于系统释放等待期（TIME_WAIT），请稍候重试或更换端口。")
            return

        httpd.serve_root = d
        httpd.allow_write = bool(self.allow_write_var.get())
        # 先登记再启动线程：确保任何异常路径下都不会出现「已绑定但无记录」的孤儿端口
        rec = ServerRecord(httpd, None, d, port,
                           bool(self.allow_write_var.get()), bool(self.pretty_var.get()))
        self.servers[port] = rec
        self.tree.insert("", "end", iid=str(port),
                         values=(port, d, self._write_text(rec.write),
                                 self._pretty_text(rec.pretty), "自检中…"))
        rec.thread = ServerThread(httpd)
        rec.thread.start()
        self._update_buttons()
        self._update_tray_status()

        # 自检在后台线程执行（不阻塞 UI、直连不走系统代理），结果经主队列回主线程
        threading.Thread(target=self._verify_worker, args=(rec,), daemon=True).start()

    def _verify_worker(self, rec):
        ok, why = self._verify_server(rec.dir, rec.port, rec.pretty)
        self._main_queue.put(("verify", rec, ok, why))

    def _post_to_main(self, fn):
        """线程安全地把一个可调用对象投递回主线程执行。"""
        self._main_queue.put(("call", fn))

    def _poll_main_queue(self):
        """主线程每 60ms 消费主队列（自检结果 / 托盘回调），worker 只入队不碰 UI。"""
        try:
            while True:
                item = self._main_queue.get_nowait()
                if item[0] == "verify":
                    _, rec, ok, why = item
                    self._finalize_start(rec, ok, why)
                elif item[0] == "call":
                    item[1]()
        except queue.Empty:
            pass
        except tk.TclError:
            return  # 窗口已销毁，停止轮询
        try:
            self.root.after(60, self._poll_main_queue)
        except tk.TclError:
            pass

    def _finalize_start(self, rec, ok, why):
        if self.servers.get(rec.port) is not rec:
            return  # 自检期间用户已停止该实例
        if not ok:
            self._stop_one(rec.port)
            self._report_error("启动失败",
                               f"端口 {rec.port} 的服务器未通过自检：\n{why}\n\n请检查端口是否被其他程序占用。")
            return
        self._refresh_row(rec.port)
        self._update_tray_status()
        lan = get_lan_ip()
        self._set_status(f"端口 {rec.port} 运行中 · {rec.dir} · 局域网 http://{lan}:{rec.port}/ · 共 {len(self.servers)} 个实例")

    def _verify_server(self, root, port, pretty):
        """校验：HTTP 200 + 本工具的 Server 头（两模式通用）；
        pretty 比对条目数标记，plain 探测首个条目（不再只认 pretty 模板标记）。"""
        # 直连，不走系统/环境代理——否则代理拦 127.0.0.1 会误杀健康实例
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            names = sorted(os.listdir(root))
        except OSError as e:
            return False, f"无法读取目录：{e}"
        last = None
        for _ in range(3):  # 绑定已同步完成，重试仅覆盖异常延迟
            try:
                with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                    server_hdr = resp.headers.get("Server", "")
                    content = resp.read().decode("utf-8", errors="ignore")
                    status = resp.status
                if status != 200:
                    last = f"HTTP {status}"
                elif "ServeDir/2.0" not in server_hdr:
                    last = "返回页面不是本工具（Server 头不符），端口可能被其他程序抢占。"
                elif pretty and f'data-count="{len(names)}"' not in content:
                    last = "列表页条目数与所选目录不符，端口可能被其他程序抢占。"
                elif not pretty and names:
                    # 探测候选跳过 __download（它是打包端点名，GET 会 404）
                    candidates = [n for n in names if n != "__download"]
                    if not candidates:
                        return True, None  # Server 头已足以证明是本工具
                    probe = urllib.parse.quote(candidates[0])
                    with opener.open(f"http://127.0.0.1:{port}/{probe}", timeout=2) as r2:
                        if r2.status == 200:
                            return True, None
                        last = f"目录条目探测失败：HTTP {r2.status}"
                else:
                    return True, None
            except Exception as e:
                last = f"访问 http://127.0.0.1:{port}/ 失败：{e}"
            time.sleep(0.25)
        return False, last

    def _selected_ports(self):
        return [int(iid) for iid in self.tree.selection()]

    def _refresh_row(self, port):
        rec = self.servers.get(port)
        if rec is None:
            return
        state = "运行中"
        try:
            self.tree.item(str(port), values=(rec.port, rec.dir, self._write_text(rec.write),
                                              self._pretty_text(rec.pretty), state))
        except tk.TclError:
            pass

    def _on_tree_double_click(self, event):
        # 仅双击有效行才打开浏览器，避免空白区域弹提示
        if self.tree.identify_row(event.y):
            self.open_browser()

    def open_browser(self):
        ports = self._selected_ports()
        if not ports:
            self._report_info("提示", "请先在列表中选择一个服务器。")
            return
        webbrowser.open(f"http://127.0.0.1:{ports[0]}/")

    def toggle_write(self):
        """热切换选中实例的 PUT 写入权限。"""
        ports = self._selected_ports()
        if not ports:
            self._report_info("提示", "请先在列表中选择要切换的服务器。")
            return
        for p in ports:
            rec = self.servers.get(p)
            if rec is None:
                continue
            rec.write = not rec.write
            rec.httpd.allow_write = rec.write
            self._refresh_row(p)
        self._set_status(f"已切换 {'、'.join(str(p) for p in ports)} 的写入状态")

    def stop_selected(self):
        ports = self._selected_ports()
        if not ports:
            self._report_info("提示", "请先在列表中选择要停止的服务器。")
            return
        for p in ports:
            self._stop_one(p)

    def stop_all(self):
        for p in list(self.servers):
            self._stop_one(p)

    def _stop_one(self, port):
        rec = self.servers.pop(port, None)
        if rec is None:
            return
        try:
            rec.thread.stop()
            rec.thread.join(timeout=3)
        except Exception:
            pass
        try:
            rec.httpd.server_close()
        except Exception:
            pass
        try:
            self.tree.delete(str(port))
        except tk.TclError:
            pass
        self._update_buttons()
        self._update_tray_status()
        self._set_status(f"端口 {port} 已停止 · 剩余 {len(self.servers)} 个实例")

    def _update_buttons(self):
        has = bool(self.servers)
        for b in (self.open_btn, self.write_btn, self.stop_btn, self.stopall_btn):
            b.config(state="normal" if has else "disabled")

    # ---------- 系统托盘 ----------

    def _setup_tray(self):
        if sys.platform != "win32":
            return
        try:
            from win_tray import TrayIcon
            self.tray = TrayIcon(
                tooltip="serve-dir · 0 个服务器运行中",
                status_fn=lambda: (f"{len(self.servers)} 个服务器运行中" if self.servers
                                   else "未运行任何服务器"),
                icon_path=sys.executable,
                on_left_click=lambda: self._post_to_main(self._toggle_window),
                menu=(("打开主窗口", lambda: self._post_to_main(self._show_window)),
                      ("退出", lambda: self._post_to_main(self._quit_from_tray))),
            )
            self.tray.start()
        except Exception:
            self.tray = None  # 托盘不可用不影响主功能

    def _update_tray_status(self):
        if self.tray is not None:
            try:
                self.tray.set_tip(f"serve-dir · {len(self.servers)} 个服务器运行中")
            except Exception:
                pass

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _toggle_window(self):
        if self.root.state() == "withdrawn":
            self._show_window()
        else:
            self._on_window_close()

    def _on_window_close(self):
        """窗口 ×：有实例在跑 → 收进托盘继续服务；否则直接退出。"""
        if self.servers and self.tray is not None:
            self.root.withdraw()
            self._update_tray_status()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.balloon("serve-dir",
                                  "已收进系统托盘，服务器继续运行。\n右键托盘图标可打开主窗口或退出。")
        else:
            self._quit_from_tray()

    def _quit_from_tray(self):
        """真正退出：停全部实例 → 删托盘图标 → 结束消息泵 → 销毁窗口。"""
        self.stop_all()
        if self.tray is not None:
            try:
                self.tray.remove()
            except Exception:
                pass
            self.tray = None
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _on_close(self):
        self._quit_from_tray()


def main():
    """GUI 主入口。

    旧版缺陷：在 python.exe 下用 sys.executable 转发自身 → 子代再次命中同一分支 →
    无限重生链。现改为：仅在 python.exe 下显式寻找 pythonw.exe 转交一次，
    并用环境变量哨兵兜底防递归；找不到 pythonw 时就地运行（带控制台，功能不受影响）。
    """
    if sys.platform == "win32" and not sys.executable.lower().endswith("pythonw.exe"):
        if os.environ.get(RELAUNCH_SENTINEL) != "1":
            script = os.path.abspath(__file__)
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if os.path.isfile(pythonw):
                env = dict(os.environ, **{RELAUNCH_SENTINEL: "1"})
                subprocess.Popen(
                    [pythonw, script],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    env=env,
                    cwd=os.path.dirname(script),
                )
                return
            # 找不到 pythonw：就地运行（保留控制台，但功能完整）
    root = tk.Tk()
    ServeDirGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
