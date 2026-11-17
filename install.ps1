# AI Desktop Ops - Windows installer
#
#   .\install.ps1                 copy hub\ + examples\ to %LOCALAPPDATA%\AIDesktopOps, create hub\apps.json, check python
#   .\install.ps1 -Autostart      also register hub at logon (HKCU\...\Run, no admin needed)
#   .\install.ps1 -Uninstall      remove the autostart entry (files are kept)
#
# It never installs a service, never touches machine-wide settings, never enables
# autostart unless you ask for it with -Autostart.
#
# The repo layout is preserved under $Target on purpose: apps.json entries use
# {HERE} (= the hub directory) and the bundled example points at
# {HERE}\..\examples\site, so flattening hub.py straight into $Target breaks it
# (that example then fails to start with WinError 267).

[CmdletBinding()]
param(
    [switch]$Autostart,
    [switch]$Uninstall,
    [string]$Target = "$env:LOCALAPPDATA\AIDesktopOps"
)

$ErrorActionPreference = 'Stop'
$RunKey  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$RunName = 'AIDesktopOps'

function Remove-Autostart {
    if (Get-ItemProperty -Path $RunKey -Name $RunName -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path $RunKey -Name $RunName
        Write-Host "  [ok] autostart entry removed"
    } else {
        Write-Host "  [--] no autostart entry"
    }
}

if ($Uninstall) {
    Write-Host "AI Desktop Ops - remove autostart"
    Remove-Autostart
    Write-Host "Files left in place: $Target"
    return
}

# --- 1. python -------------------------------------------------------------
Write-Host "AI Desktop Ops - installer"
$py = $null
foreach ($cand in @('python', 'py')) {
    try {
        $ver = & $cand --version 2>&1
        if ($LASTEXITCODE -eq 0) { $py = $cand; break }
    } catch { }
}
if (-not $py) {
    Write-Warning "Python not found on PATH. Install Python 3.8+ first: https://www.python.org/downloads/"
    exit 1
}
Write-Host "  [ok] python: $py ($ver)"

# --- 2. copy hub + examples (repo layout kept) ------------------------------
if (-not (Test-Path "$PSScriptRoot\hub\hub.py")) {
    Write-Error "run this script from the repo root (hub\hub.py not found)"
    exit 1
}
New-Item -ItemType Directory -Force -Path "$Target\hub"        | Out-Null
New-Item -ItemType Directory -Force -Path "$Target\hub\logs"   | Out-Null
Copy-Item "$PSScriptRoot\hub\hub.py"            "$Target\hub\hub.py"            -Force
Copy-Item "$PSScriptRoot\hub\index.html"        "$Target\hub\index.html"        -Force
Copy-Item "$PSScriptRoot\hub\apps.example.json" "$Target\hub\apps.example.json" -Force

if (Test-Path "$PSScriptRoot\examples") {
    New-Item -ItemType Directory -Force -Path "$Target\examples" | Out-Null
    Copy-Item "$PSScriptRoot\examples\*" "$Target\examples\" -Recurse -Force
}
if (Test-Path "$PSScriptRoot\plugin\plugin.js") {
    New-Item -ItemType Directory -Force -Path "$Target\plugin" | Out-Null
    Copy-Item "$PSScriptRoot\plugin\plugin.js" "$Target\plugin\plugin.js" -Force
}
Write-Host "  [ok] hub copied to $Target\hub (examples\ and plugin\ alongside)"

# --- 3. apps.json ----------------------------------------------------------
if (Test-Path "$Target\hub\apps.json") {
    Write-Host "  [--] hub\apps.json already exists, kept"
} else {
    Copy-Item "$Target\hub\apps.example.json" "$Target\hub\apps.json"
    Write-Host "  [ok] hub\apps.json created from the example"
}

# --- 4. run hint -----------------------------------------------------------
$hub = Join-Path $Target 'hub'
Write-Host ""
Write-Host "Start the hub:"
Write-Host "    cd `"$hub`"; $py hub.py"
Write-Host "Open the workbench:  http://127.0.0.1:7777"
Write-Host ""
Write-Host "Panel (optional): copy $Target\plugin\plugin.js into your host's plugin folder,"
Write-Host "then restart the host app. See docs\INSTALL.md."

# --- 5. autostart (opt-in) -------------------------------------------------
if ($Autostart) {
    $pythonw = Join-Path (Split-Path (Get-Command $py).Source) 'pythonw.exe'
    if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command $py).Source }
    $cmd = '"' + $pythonw + '" "' + (Join-Path $hub 'hub.py') + '"'
    New-ItemProperty -Path $RunKey -Name $RunName -Value $cmd -PropertyType String -Force | Out-Null
    Write-Host "  [ok] autostart registered: $cmd"
}
