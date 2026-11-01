#!/bin/bash
# serve-dir — macOS 双击入口（Finder 双击本文件即可启动 GUI）。
#
# 首次使用请在终端执行一次：
#     chmod +x scripts/serve_dir_gui.command
#
# 说明：
# - 双击会短暂打开一个终端窗口；GUI 启动后可关闭终端，不影响运行。
# - 想在前台查看日志，请改为在终端直接运行：
#       python3 scripts/serve_dir_gui.py
# - 菜单栏图标需要 pyobjc（可选）：  pip3 install pyobjc

cd "$(dirname "$0")/.." || exit 1
nohup /usr/bin/env python3 scripts/serve_dir_gui.py >/dev/null 2>&1 &
exit 0
