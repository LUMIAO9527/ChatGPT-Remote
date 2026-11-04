[CmdletBinding()]
param([switch]$InstallDependencies, [string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root '_wip\desktop-runtime'
$temporary = Join-Path $runtime 'temp'
New-Item -ItemType Directory -Path $temporary -Force | Out-Null
$env:TEMP = $env:TMP = $temporary
$env:PYTHONDONTWRITEBYTECODE = '1'
$libraries = Join-Path $runtime 'libs'
if (!$PythonPath) {
    $localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $PythonPath = if (Test-Path -LiteralPath $localPython) { $localPython } else { (& py -3 -B -c 'import sys; print(sys.executable)').Trim() }
}
if ($InstallDependencies) {
    & $PythonPath -B -m pip install --no-cache-dir --no-compile --target $libraries -r (Join-Path $PSScriptRoot 'requirements-desktop.txt')
    if ($LASTEXITCODE) { throw 'Desktop dependency installation failed.' }
}
$env:PYTHONPATH = $libraries
& $PythonPath -B -c 'import webview, PIL, qrcode; from webview.platforms import winforms; print("Desktop components are ready.")'
if ($LASTEXITCODE) { throw 'Run this script with -InstallDependencies to install the desktop components inside this project.' }
$windowlessPython = Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
if (!(Test-Path -LiteralPath $windowlessPython)) { throw 'pythonw.exe is unavailable beside Python.' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $root 'ChatGPT Remote.lnk'))
$shortcut.TargetPath = $windowlessPython
$shortcut.Arguments = '-B "' + (Join-Path $PSScriptRoot 'desktop_app.py') + '"'
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = (Join-Path $PSScriptRoot 'desktop.ico') + ',0'
$shortcut.Description = 'ChatGPT Remote'
$shortcut.Save()
Write-Output 'ChatGPT Remote.lnk is ready.'
