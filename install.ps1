# App Dock - Windows installer
#
#   .\install.ps1                 copy hub to %LOCALAPPDATA%\AppDock, create apps.json, check python
#   .\install.ps1 -Autostart      also register hub at logon (HKCU\...\Run, no admin needed)
#   .\install.ps1 -Uninstall      remove the autostart entry (files are kept)
#
# It never installs a service, never touches machine-wide settings, never enables
# autostart unless you ask for it with -Autostart.

[CmdletBinding()]
param(
    [switch]$Autostart,
    [switch]$Uninstall,
    [string]$Target = "$env:LOCALAPPDATA\AppDock"
)

$ErrorActionPreference = 'Stop'
$RunKey  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$RunName = 'AppDock'

function Remove-Autostart {
    if (Get-ItemProperty -Path $RunKey -Name $RunName -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path $RunKey -Name $RunName
        Write-Host "  [ok] autostart entry removed"
    } else {
        Write-Host "  [--] no autostart entry"
    }
}

if ($Uninstall) {
    Write-Host "App Dock - remove autostart"
    Remove-Autostart
    Write-Host "Files left in place: $Target"
    return
}

# --- 1. python -------------------------------------------------------------
Write-Host "App Dock - installer"
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

# --- 2. copy hub -----------------------------------------------------------
if (-not (Test-Path "$PSScriptRoot\hub\hub.py")) {
    Write-Error "run this script from the repo root (hub\hub.py not found)"
    exit 1
}
New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item "$PSScriptRoot\hub\hub.py"           "$Target\hub.py"           -Force
Copy-Item "$PSScriptRoot\hub\index.html"       "$Target\index.html"       -Force
Copy-Item "$PSScriptRoot\hub\apps.example.json" "$Target\apps.example.json" -Force
New-Item -ItemType Directory -Force -Path "$Target\logs" | Out-Null
Write-Host "  [ok] hub copied to $Target"

# --- 3. apps.json ----------------------------------------------------------
if (Test-Path "$Target\apps.json") {
    Write-Host "  [--] apps.json already exists, kept"
} else {
    Copy-Item "$Target\apps.example.json" "$Target\apps.json"
    Write-Host "  [ok] apps.json created from the example"
}

# --- 4. run hint -----------------------------------------------------------
Write-Host ""
Write-Host "Start the hub:"
Write-Host "    cd `"$Target`"; $py hub.py"
Write-Host "Open the workbench:  http://127.0.0.1:7777"
Write-Host ""
Write-Host "Panel (optional): copy plugin\plugin.js into your host's plugin folder,"
Write-Host "then restart the host app. See docs\INSTALL.md."

# --- 5. autostart (opt-in) -------------------------------------------------
if ($Autostart) {
    $pythonw = Join-Path (Split-Path (Get-Command $py).Source) 'pythonw.exe'
    if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command $py).Source }
    $cmd = '"' + $pythonw + '" "' + (Join-Path $Target 'hub.py') + '"'
    New-ItemProperty -Path $RunKey -Name $RunName -Value $cmd -PropertyType String -Force | Out-Null
    Write-Host "  [ok] autostart registered: $cmd"
}
