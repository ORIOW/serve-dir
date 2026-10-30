# serve-dir

Open any local directory as a LAN-accessible HTTP file server — from a single fixed script, no copies to share.

- **Browse / download** — styled listing (default) or plain native listing, with subdirectory navigation.
- **Multi-select zip download** — tick files/folders in the browser and hit 「下载所选」; the server streams a zip from `/__download?names=…`.
- **Optional write** — enable it to let clients `PUT` uploads (`curl -T file http://<host>:<port>/name.txt`). **Read-only by default**, with path-escape protection and a 1 GiB per-request cap.
- **GUI tool** — run **multiple servers at once** (different paths/ports), no console window, can hide to the system tray and stay resident.

## Files

```
scripts/serve_dir.py        # CLI version (the GUI reuses all of its server logic)
scripts/serve_dir_gui.py    # GUI main program
scripts/serve_dir_gui.pyw   # Double-click entry point (no console)
scripts/win_tray.py         # Pure-ctypes tray icon helper
SKILL.md                    # Agent-facing skill reference
tests/                      # Regression drivers (see below)
serve-dir-GUI-验证报告.html  # GUI delivery verification report
```

The scripts live in **one fixed place** and take the target directory via `--dir`, so **no directory ever needs a copy of the script**.

## CLI usage

```bash
python scripts/serve_dir.py --dir "<directory>" [--port 8080] [--host 0.0.0.0] [--plain] [--allow-write]
```

| Option | Default | Description |
|---|---|---|
| `--dir` | required | Directory to share (absolute or relative, `~` expanded) |
| `--port` | `8080` | Listen port |
| `--host` | `0.0.0.0` | Bind address; `0.0.0.0` = all interfaces (LAN accessible) |
| `--plain` | off | Plain native listing instead of the styled one (multi-select download still works) |
| `--allow-write` | off | Allow client `PUT` upload (read-only by default; 1 GiB per request) |

Starting prints both the local and LAN URLs plus the write state. If the port is occupied or still in TIME_WAIT, binding fails loudly instead of silently double-binding.

## GUI usage

| Way | Command / action | Console |
|---|---|---|
| Double-click entry (recommended) | Double-click `scripts/serve_dir_gui.pyw` | none |
| Explicit pythonw | `pythonw scripts/serve_dir_gui.py` | none |
| Plain python | `python scripts/serve_dir_gui.py` | appears, but auto-re-execs to pythonw when found |

GUI options when **starting a new server**: directory (+ browse), port spinbox (1–65535, invalid input blocked), allow client writes, pretty listing toggle.

**Multiple instances**: each 「启动新服务器」 adds an independent server; the running list shows one row per instance (port/directory/write/style/state) and you can serve several paths at once. Select a row to open its browser, hot-toggle write permission, or stop it; 「停止全部」 clears everything. Each server needs its own port.

**Startup**: the main thread binds synchronously (occupied/duplicate/invalid input pops a dialog immediately — no fake "running"), then a background thread serves and self-checks (non-blocking, direct connection, no system proxy): confirms the answer comes from this tool (`Server` header), compares item counts for pretty, probes the first entry for plain.

**Tray**: closing the window (×) **always** hides to tray — with or without running servers — and the process never exits that way (bubble notification on first hide); left-click tray icon = show/hide main window; right-click menu = open main window / **quit** (quit is the only way to exit: it stops all instances and removes the icon). The tooltip shows the live instance count. If the tray is unavailable (load failure / non-Windows), closing the window deliberately falls back to direct exit — otherwise the window would vanish with no way to quit from the UI.

## Web features

- **Multi-select download**: every row has a checkbox; 「全选」 + 「下载所选（N）」 → `GET /__download?names=<relpath>&names=…` (URL-encoded, repeated params). Directories are zipped recursively. Path-escape checks reject `../`, absolute paths and cross-drive paths.
- **Upload** (when writing is enabled): `PUT /<relpath>` with the request body as file content; missing parent directories are created inside the served root; out-of-bounds → 403, over-limit → 413, success → 201. Example: `curl -T local.txt http://<host>:<port>/uploaded.txt`

## Port binding and restart semantics

- The socket uses Windows `SO_EXCLUSIVEADDRUSE` exclusive binding, so **double-binding can never happen** (the old risk is gone).
- Cost: right after stopping, an immediate restart may hit TIME_WAIT (roughly 2–4 minutes) and report the port as occupied. Wait a bit or use another port; both GUI and CLI report this clearly rather than failing silently.
- Occupancy detection is based on "can a connection be established" (only a real listener accepts connections, so TIME_WAIT never false-positives).

## Testing

Regression drivers live in `tests/`. Run them from the **skill root** (the drivers rely on `os.path.abspath('scripts')`):

```bash
python tests/sd_fix_test.py      # 35 checks (single instance)
python tests/sd_multi_test.py   # 23 checks (multi instance)
python tests/sd_review_test.py  # 15 checks (review fixes)
python tests/sd_tray_test.py    # 16 checks (tray)
pythonw tests/sd_tray_pt.py     # real-machine full chain; log to %TEMP%\sd_tray_pt.log
python tests/sd_pythonw_repro.py # reproduces the pythonw stderr=None bug
```

Expected: `FAILED: none` for every suite. Temporary artifacts go to `%TEMP%\sd_*`.

> The tray suite needs a real interactive Windows session: `Shell_NotifyIconW(NIM_ADD)` returns 0 in headless/sandboxed processes, so tray checks can fail for environmental reasons even when the code is correct.

## Security notes

- **Binding `0.0.0.0` opens the directory to the LAN** — make sure it contains no secrets or personal files before sharing.
- **Enabling write means anyone can upload** — anyone on the network may overwrite files or fill the disk (1 GiB per request). Turn it off when done.
- This tool performs **no authentication**. Do not use it for production hosting or access-controlled sharing.

## Known limitations

- No remembered last configuration in the GUI; not packaged as an .exe; no autostart.
- A fast stop → restart can hit the TIME_WAIT occupation (a deliberate trade-off for exclusive binding).
- Tray right-click menu has not been manually verified on a real machine.

## License

MIT — see [LICENSE](LICENSE).
