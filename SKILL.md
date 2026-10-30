---
name: serve-dir
description: "Open a local directory as a LAN-accessible HTTP file server — styled listing with multi-select zip download, optional PUT upload (read-only by default), or plain native listing — using fixed scripts in this skill, no per-directory copies. Use when the user wants to share a folder over the local network (e.g. 'serve this folder', 'open a python server on this dir', 'let other machines fetch/upload these files'). GUI tool included (no console window). Handles detecting and stopping any existing server on the target port before starting the new one. Applies on Windows host. Do NOT use for production hosting or authenticated/access-controlled sharing."
---

# serve-dir — 单脚本目录文件服务器

把"在某个目录开一个局域网可访问的 Python 文件服务器"做成可复用流程。脚本只有**一份**，固定在 `scripts/`，通过 `--dir` 传要分享的目录，因此**任何目录都不需要放脚本副本**。

## 功能一览

- **浏览/下载**：样式化列表（默认）或朴素原生列表；子目录导航。
- **多选打包下载**：网页端勾选多个文件/文件夹 → 「下载所选」→ 服务端 `/__download?names=…` 流式打 zip。
- **可选写入**：开启后 client 可用 `PUT` 上传（`curl -T 文件 URL`），默认**只读**；路径逃逸防护 + 单次上限 1 GiB。
- **GUI 小工具**：图形界面，支持**同时运行多个 server**（不同路径/端口），无控制台窗口，可收进系统托盘长期驻留。

## 适用判断

- 想通过 HTTP 把某个本地目录分享给本机或局域网其它机器（浏览/下载，可选上传）。
- 想换目录、换端口开 server，但不想在目标目录里塞脚本副本。
- 不适用：生产环境部署、需要鉴权/访问控制的共享。

## 脚本位置（固定，请勿复制）

```
skills/serve-dir/scripts/serve_dir.py        # CLI 版（GUI 复用其全部服务端逻辑）
skills/serve-dir/scripts/serve_dir_gui.py    # GUI 版主程序
skills/serve-dir/scripts/serve_dir_gui.pyw   # GUI 双击入口（无控制台）
```

## CLI 启动

```bash
python skills/serve-dir/scripts/serve_dir.py --dir "<目录>" [--port 8080] [--host 0.0.0.0] [--plain] [--allow-write]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--dir` | 必填 | 要分享的目录（绝对或相对，支持 `~` 展开） |
| `--port` | `8080` | 监听端口 |
| `--host` | `0.0.0.0` | 绑定地址，`0.0.0.0` = 所有接口（局域网可访问） |
| `--plain` | 关 | 用朴素原生列表而非样式化列表（多选下载端点仍可用） |
| `--allow-write` | 关 | 允许 client `PUT` 上传（默认只读；单次上限 1 GiB） |

启动即打印本机与局域网访问地址与写入状态。端口被占用或处于 TIME_WAIT 释放等待期时，绑定会明确报错退出（不会静默双绑定）。

## GUI 启动（推荐给长期驻留使用）

| 方式 | 命令 / 操作 | 控制台 |
|---|---|---|
| 双击入口（推荐） | 双击 `scripts/serve_dir_gui.pyw` | 无 |
| 显式 pythonw | `pythonw skills/serve-dir/scripts/serve_dir_gui.py` | 无 |
| 普通 python | `python skills/serve-dir/scripts/serve_dir_gui.py` | 有，但会自动转交 pythonw（找到 pythonw.exe 时）；转交失败则就地运行 |

> 注意：普通 `python` 启动依赖解释器同目录的 `pythonw.exe`（标准 Windows 安装都有）。GUI 不再做任何自我重生循环。

GUI 参数（「启动新服务器」时生效于新实例）：

| 控件 | 默认 | 说明 |
|---|---|---|
| 目录输入框 + 浏览 | 当前目录 | 回车 = 应用手输路径 |
| 端口 Spinbox | `8080` | 1–65535；非数字/越界会被拦截提示 |
| 允许 client 写入 | 关 | 只影响 PUT |
| 使用 pretty 样式列表 | 开 | 关闭后用朴素原生列表 |

多实例：每次点「启动新服务器」都会按当前配置新增一个独立 server，运行列表逐行显示端口/目录/写入/样式/状态，可同时服务多个路径（同一目录也可在多个端口重复开）。选中行后：「打开浏览器」（或双击该行）打开该实例、「切换写入」热切换该实例的 PUT 权限、「停止选中」停止所选；「停止全部」一键清空。每个服务器需要独立端口。

启动行为：主线程**同步绑定**端口（占用/重复/非法输入立即弹窗，不再假"运行中"）→ 后台线程服务 → 后台自检（不阻塞界面、直连不走系统代理）：确认应答来自本工具（Server 头），pretty 比对条目数、plain 探测首个条目。

托盘：关闭窗口（×）＝收进系统托盘，服务器继续运行（首次收起有气泡提示）；左键托盘图标＝显示/隐藏主窗口；右键菜单＝打开主窗口 / 退出（退出会停止全部实例并删除图标）。托盘提示实时显示运行中的实例数。无实例在跑时关闭窗口＝直接退出；托盘不可用（非 Windows/加载失败）时自动降级为直接退出。

## 网页端功能

- **多选下载**：列表页每行带复选框；「全选」+「下载所选（N）」→ `GET /__download?names=<相对路径>&names=…`（URL 编码，可重复参数）；目录会递归打包；服务端做路径逃逸校验（`../`、绝对路径、跨盘一律拒绝）。
- **上传**（开启写入后）：`PUT /<相对路径>`，请求体即文件内容；父目录不存在会自动创建（限服务目录内）；越界 403、超限 413、成功 201。例：`curl -T 本地文件.txt http://<host>:<port>/上传名.txt`

## 标准流程（agent 每次都走）

1. **确认目录存在**：`Test-Path "<dir>"`。不存在则停下告知用户。
2. **检测目标端口是否已有 server**：
   - `Get-NetTCPConnection -LocalPort <port> -State Listen`（沙箱内可能受限）或 `netstat -ano | findstr ":<port>"`，也可 `curl -s -o NUL -w "%{http_code}" http://127.0.0.1:<port>/` 探活。
   - 沙箱 netstat 常看不到用户会话里的进程——探活以「能否连上」为准，不要凭 netstat 空结果判"端口空闲"。
3. **关闭旧 server**：
   - agent 本会话内启动的：直接停止对应后台作业。
   - 用户在外部终端启动的：沙箱通常无权限终止（WinError 5），把命令交给用户执行并要求回贴确认：
     ```powershell
     netstat -ano | findstr ":<port>"
     taskkill /PID <PID> /F
     ```
4. **启动新 server**（后台作业）：`python skills/serve-dir/scripts/serve_dir.py --dir "<目录>" --port <port>`
5. **验证**：访问 `http://127.0.0.1:<port>/`，确认 HTTP 200，且页面 `data-count="<N>"` 与目录条目数一致（或核对列表里的特征文件名）。
6. **告知访问地址**：本机 `http://127.0.0.1:<port>/`；局域网 `http://<本机LAN IP>:<port>/`，并说明当前是否只读。

## 端口占用与重启语义

- 服务器套接字使用 Windows `SO_EXCLUSIVEADDRUSE` 独占绑定：**不可能出现双绑定抢流量**（旧版风险已消除）。
- 代价：刚停止后立即重启可能遇到 TIME_WAIT（约 2–4 分钟）报占用。等一会或换端口；GUI/CLI 都会给出明确错误而非静默异常。
- 占用探测以「能否建立连接」为准（真实监听者才接受连接，TIME_WAIT 不误报）。

## 事故教训（务必遵守）

- **先确认"在哪台机器开 server"**：agent 所在主机与用户口中的目标机可能不是同一台。开服前先用 `ipconfig` 确认本机 IP，并用 `curl` 探活目标地址确认运行机。
- **目录参数化，不复制脚本**：永远用 `--dir` 传目录。
- **跨会话进程杀不掉**：用户在外部终端起的 server，agent 沙箱无权限终止，必须交还命令让用户执行并回贴确认。
- **绑定 `0.0.0.0` 即向局域网开放**：确认被分享的目录不含密钥/隐私文件；本机 `old AI files` 目录就含多个 API key 文本，分享前提醒用户。
- **开启写入 = 任何人可上传**：确认目录可被覆盖/撑满（上限 1 GiB/次），用完即关。
- **改完代码先清链再测**：旧版曾留下无限重生链；测试 GUI 前先确认没有遗留的 `serve_dir_gui` 进程。

## 后台作业生命周期

### Agent 会话管理的 server

- 通过后台作业启动的 server 随 **agent 会话**存活；若需**常驻**，让用户在自己的 PowerShell 前台运行脚本，或直接用 GUI（.pyw）。
- 需要停止时：会话内用 `job_kill`；外部起的按上面的 taskkill 流程。

### GUI 管理的 server

- GUI 使用后台线程服务；关闭 GUI 窗口即停止 server。
- 若启动失败，GUI 会弹窗给出真实原因（端口被占用 / TIME_WAIT / 目录无效 / 端口非数字）。
