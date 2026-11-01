# build_windows_exe.ps1 — Build the serve-dir GUI as a self-contained Windows onedir exe (PyInstaller).
# Mirrors scripts/build_macos_app.sh on the macOS side. Run from anywhere; it resolves the repo root itself.
#
# Requirements: Python 3.9+ on PATH, and `pip install pyinstaller`.
# Output: <root>\dist\serve_dir_gui\serve_dir_gui.exe  (+ _internal\)
#
# Why onedir (not onefile): onedir does no runtime extraction, so it is verifiable inside
# restricted/sandboxed environments (onefile's on-launch DLL extraction can be blocked there)
# and it is the PyInstaller-recommended production layout. The folder is zipped for release.
#
# Usage:
#   pwsh scripts\build_windows_exe.ps1            # build only
#   pwsh scripts\build_windows_exe.ps1 -Zip       # build + zip to dist\serve_dir_gui-win64.zip

[CmdletBinding()]
param(
    [switch]$Zip
)

$ErrorActionPreference = "Stop"

# Repo root = parent of this scripts/ directory.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- Locate python ---------------------------------------------------------
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) { $pyCmd = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $pyCmd) { throw "Python not found on PATH. Install Python 3.9+ and re-run." }
$python = $pyCmd.Source

# --- Check PyInstaller -----------------------------------------------------
& $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Run: `"$python`" -m pip install pyinstaller"
}

# --- Clean previous outputs ------------------------------------------------
foreach ($p in @("$root\dist\serve_dir_gui", "$root\build")) {
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}

# --- Build (onedir, no console) --------------------------------------------
& $python -m PyInstaller `
    --onedir --noconsole `
    --name serve_dir_gui `
    --distpath dist --workpath build --specpath build `
    --paths scripts `
    --hidden-import win_tray `
    --exclude-module mac_tray `
    --exclude-module AppKit `
    --exclude-module Foundation `
    scripts/serve_dir_gui.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

$exe = Join-Path $root "dist\serve_dir_gui\serve_dir_gui.exe"
if (-not (Test-Path $exe)) { throw "Build did not produce $exe" }
Write-Host "Built: $exe ($((Get-Item $exe).Length) bytes)"

# --- Optional zip (contents of the onedir folder, exe at the zip root) -----
# NOTE: the variable is $zipPath (not $zip) on purpose — PowerShell variable names are
# case-insensitive, so $zip would collide with the [switch]$Zip parameter above and
# assigning a string path to it fails with "cannot convert String to SwitchParameter".
if ($Zip) {
    $zipPath = "$root\dist\serve_dir_gui-win64.zip"
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    Compress-Archive -Path "$root\dist\serve_dir_gui\*" -DestinationPath $zipPath
    Write-Host "Zipped: $zipPath ($((Get-Item $zipPath).Length) bytes)"
}
