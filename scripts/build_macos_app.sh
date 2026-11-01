#!/bin/bash
# build_macos_app.sh — 把 serve-dir GUI 打包成独立 .app（Finder 双击启动，无终端窗口）。
#
# 原理：不打包 Python（那需要 py2app，体积大配置重），而是生成一个最小 App 包，
# 其可执行文件是几行的启动脚本：LaunchServices 直接 exec python3 运行 GUI，
# 全程不经过 Terminal；Info.plist 里 LSUIElement 让 App 成为纯代理应用
# （无 Dock 图标），窗口照常显示，常驻行为由菜单栏图标承担。
#
# 用法：bash scripts/build_macos_app.sh   （产物 dist/ServeDir.app）
# 注意：启动器内写入本仓库 scripts/serve_dir_gui.py 的绝对路径；
#       仓库移动位置后重跑本脚本即可更新。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUI="$ROOT/scripts/serve_dir_gui.py"
APP="$ROOT/dist/ServeDir.app"

[ -f "$GUI" ] || { echo "找不到 $GUI" >&2; exit 1; }

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/scripts"

# GUI 运行所需的脚本打 cohesive 进 bundle（自包含：.app 拷到任何机器/路径都能跑，
# 不依赖本仓库位置）。macOS 用不到 win_tray.py / serve_dir_gui.pyw，不打入。
for f in serve_dir.py serve_dir_gui.py mac_tray.py; do
    cp "$ROOT/scripts/$f" "$APP/Contents/Resources/scripts/"
done

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>ServeDir</string>
    <key>CFBundleDisplayName</key><string>serve-dir</string>
    <key>CFBundleIdentifier</key><string>org.oriow.serve-dir</string>
    <key>CFBundleVersion</key><string>1.1.0</string>
    <key>CFBundleShortVersionString</key><string>1.1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>ServeDir</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>LSUIElement</key><true/>
    <key>NSHighResolutionCapable</key><true/>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/ServeDir" <<LAUNCH
#!/bin/bash
# 本文件由 scripts/build_macos_app.sh 生成，请勿手改。
# GUI 用 bundle 内自带的脚本副本（相对本启动器定位），.app 放哪都能跑。
RES="\$(cd "\$(dirname "\$0")/../Resources" && pwd)"
PY=""
# 注意：Homebrew 的 python@3.12 不创建 /opt/homebrew/bin/python3，
# 未加版本的 python3 只存在于 libexec shim 目录，必须优先探测。
for p in \\
    /opt/homebrew/opt/python@3.12/libexec/bin/python3 \\
    /opt/homebrew/opt/python@3.13/libexec/bin/python3 \\
    /opt/homebrew/opt/python@3.11/libexec/bin/python3 \\
    /opt/homebrew/bin/python3 \\
    /usr/local/bin/python3 \\
    /usr/bin/python3; do
    if [ -x "\$p" ]; then PY="\$p"; break; fi
done
if [ -z "\$PY" ]; then
    osascript -e 'display dialog "ServeDir：未找到可用的 python3，请先安装 Homebrew Python（brew install python@3.12 python-tk@3.12）" buttons {"好"} with icon stop' >/dev/null 2>&1
    exit 1
fi
exec "\$PY" "\$RES/scripts/serve_dir_gui.py"
LAUNCH
chmod +x "$APP/Contents/MacOS/ServeDir"

# 图标：SF Symbols server.rack → icns（失败则跳过，保持系统默认图标）
if python3 "$ROOT/scripts/make_app_icon.py" "$APP/Contents/Resources/AppIcon.icns"; then
    echo "应用图标：server.rack"
else
    echo "警告：应用图标生成失败（缺 pyobjc？），保持系统默认图标" >&2
fi

echo "已生成 ${APP}（自包含：脚本已打入 Resources/scripts/，可分发到任意 Mac）"
echo "Finder 双击 ServeDir.app 即可启动（无终端窗口；菜单栏出现 server.rack 图标）"
