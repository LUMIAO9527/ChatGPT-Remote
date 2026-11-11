[CmdletBinding()]
param([string]$PythonPath='', [string]$DependencyPath='', [string]$OutputDirectory='', [string]$BuildDirectory='', [switch]$DiagnosticConsole)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
if(!$PythonPath){$PythonPath=(& py -3 -B -c 'import sys; print(sys.executable)').Trim()}
$batch=if($BuildDirectory){[IO.Path]::GetFullPath($BuildDirectory)}else{Join-Path $root '_wip\build'}
$temporary=Join-Path $batch 'temp'
New-Item -ItemType Directory -Path $temporary -Force | Out-Null
$env:TEMP=$env:TMP=$temporary
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYINSTALLER_CONFIG_DIR=Join-Path $batch 'pyinstaller-cache'
if(!$DependencyPath){
  $DependencyPath=Join-Path $batch 'libs'
  & $PythonPath -B -m pip install --no-cache-dir --no-compile --target $DependencyPath -r (Join-Path $root 'WindowsAgent\requirements-build.txt')
  if($LASTEXITCODE){throw 'Build dependency installation failed.'}
}
$env:PYTHONPATH=$DependencyPath
if(!$OutputDirectory){$OutputDirectory=Join-Path $root '_wip\release'}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$agent=Join-Path $root 'WindowsAgent'
$versionInfo=Join-Path $batch 'version-info.txt'
$versionScript=@'
from pathlib import Path
import sys
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
sys.path.insert(0, sys.argv[1])
from app_version import APP_VERSION
parts = tuple(int(value) for value in APP_VERSION.split('.'))
parts = (parts + (0, 0, 0, 0))[:4]
fields = [('CompanyName', 'LUMIAO9527'), ('FileDescription', 'ChatGPT Remote'),
          ('FileVersion', '.'.join(map(str, parts))), ('InternalName', 'ChatGPTRemote'),
          ('OriginalFilename', 'ChatGPTRemote.exe'), ('ProductName', 'ChatGPT Remote'),
          ('ProductVersion', '.'.join(map(str, parts)))]
info = VSVersionInfo(ffi=FixedFileInfo(filevers=parts, prodvers=parts, mask=0x3f,
    flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), kids=[
    StringFileInfo([StringTable('080404b0', [StringStruct(key, value) for key, value in fields])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])])
Path(sys.argv[2]).write_text(str(info), encoding='utf-8')
'@
$versionScript | & $PythonPath -B - $agent $versionInfo
if($LASTEXITCODE){throw 'Windows version metadata generation failed.'}
$manifest=Join-Path $batch 'netfx-bundle.json'
$helper=Join-Path $batch 'ChatGPTRemote.Update.exe'
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /reference:System.Web.Extensions.dll (('/out:')+$helper) (Join-Path $agent 'update_common.cs') (Join-Path $agent 'update_helper.cs')
if($LASTEXITCODE){throw 'Update helper compilation failed.'}
$rail=Join-Path $batch 'ChatGPTRemote.Rail.dll'
$railRefs=@('UIAutomationClient.dll','UIAutomationTypes.dll','WindowsBase.dll') | ForEach-Object {
  '/reference:'+(Join-Path (Split-Path -Parent $compiler) ('WPF\'+$_))
}
& $compiler /nologo /target:library /platform:x64 /optimize+ /utf8output /codepage:65001 /reference:System.Drawing.dll /reference:System.Windows.Forms.dll @railRefs ('/out:'+$rail) (Join-Path $agent 'sidebar_button.cs')
if($LASTEXITCODE){throw 'Sidebar button compilation failed.'}
& $PythonPath -B (Join-Path $agent 'build_runtime_manifest.py') $manifest $rail
if($LASTEXITCODE){throw 'Desktop runtime inventory failed.'}
$arguments=@('-B','-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed','--name','ChatGPTRemote.Core',
  '--distpath',(Join-Path $batch 'dist'),'--workpath',(Join-Path $batch 'work'),'--specpath',$batch,
  '--paths',$agent,'--paths',$DependencyPath,'--icon',(Join-Path $agent 'desktop.ico'),
  '--version-file',(Join-Path $batch 'version-info.txt'),
  '--hidden-import','webview.platforms.winforms','--hidden-import','qrcode',
  '--collect-all','webview','--collect-all','pythonnet','--collect-all','clr_loader',
  '--add-binary',($helper+';.'),'--add-binary',($rail+';.'),'--add-data',((Join-Path $root 'THIRD_PARTY_NOTICES.md')+';.'),
  '--add-data',($manifest+';.'),'--add-data',((Join-Path $agent 'netfx.config')+';.'))
foreach($name in @('desktop.html','desktop.ico','desktop-light.ico','desktop-dark.ico','assets','web','uia_reader.ps1','manage_frozen.ps1','uninstall.ps1','legacy_tasks.ps1')){
  $arguments+=@('--add-data',((Join-Path $agent $name)+';.'))
  if($name -in @('assets','web')){$arguments[$arguments.Count-1]=(Join-Path $agent $name)+';'+$name}
}
$arguments+=Join-Path $agent 'remote_main.py'
$hasWgc = $false
try { & $PythonPath -c "import windows_capture" 2>$null; $hasWgc = ($LASTEXITCODE -eq 0) } catch { $hasWgc = $false }
if($hasWgc){
  $arguments+=@('--hidden-import','windows_capture','--collect-binaries','windows_capture')
}
if($DiagnosticConsole){$arguments[$arguments.IndexOf('--windowed')]='--console'}
& $PythonPath @arguments
if($LASTEXITCODE){throw 'Program packaging failed.'}
$program=Join-Path $OutputDirectory 'ChatGPTRemote.exe'
& $PythonPath -B (Join-Path $agent 'build_launcher.py') (Join-Path $batch 'dist\ChatGPTRemote.Core') $program
if($LASTEXITCODE){throw 'Launcher packaging failed.'}
$report=Join-Path $batch 'program-self-test.json'
if(Test-Path -LiteralPath $report){Remove-Item -LiteralPath $report -Force}
$start=[Diagnostics.ProcessStartInfo]::new()
$start.FileName=$program
$start.Arguments='--self-test --report "'+$report+'"'
$start.WorkingDirectory=$root
$start.UseShellExecute=$false
$start.CreateNoWindow=$true
$start.RedirectStandardError=$true
$start.RedirectStandardOutput=$true
$check=[Diagnostics.Process]::Start($start)
$stderr=$check.StandardError.ReadToEndAsync()
$stdout=$check.StandardOutput.ReadToEndAsync()
if(!$check.WaitForExit(60000)){throw 'Packaged program verification timed out.'}
[IO.File]::WriteAllText((Join-Path $batch 'self-test-runtime.log'),$stdout.Result+$stderr.Result,[Text.UTF8Encoding]::new($false))
if($check.ExitCode -or !(Test-Path -LiteralPath $report)){throw 'Packaged program verification failed.'}
Write-Output $program
# Packaging is verified above; do not let a stale native exit code turn a
# successful build into a failure for the caller.
exit 0
