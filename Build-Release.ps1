[CmdletBinding()]
param([string]$PythonPath='', [string]$DependencyPath='', [string]$OutputDirectory='', [switch]$DiagnosticConsole)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
if(!$PythonPath){$PythonPath=(& py -3 -B -c 'import sys; print(sys.executable)').Trim()}
$batch=Join-Path $root '_wip\build'
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
$manifest=Join-Path $batch 'netfx-bundle.json'
& $PythonPath -B (Join-Path $agent 'build_runtime_manifest.py') $manifest
if($LASTEXITCODE){throw 'Desktop runtime inventory failed.'}
$helper=Join-Path $batch 'ChatGPTRemote.Update.exe'
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /reference:System.Web.Extensions.dll (('/out:')+$helper) (Join-Path $agent 'update_common.cs') (Join-Path $agent 'update_helper.cs')
if($LASTEXITCODE){throw 'Update helper compilation failed.'}
$arguments=@('-B','-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed','--name','ChatGPTRemote.Core',
  '--distpath',(Join-Path $batch 'dist'),'--workpath',(Join-Path $batch 'work'),'--specpath',$batch,
  '--paths',$agent,'--paths',$DependencyPath,'--icon',(Join-Path $agent 'desktop.ico'),
  '--version-file',(Join-Path $batch 'version-info.txt'),
  '--hidden-import','webview.platforms.winforms','--hidden-import','qrcode',
  '--collect-all','webview','--collect-all','pythonnet','--collect-all','clr_loader',
  '--add-binary',($helper+';.'),'--add-data',((Join-Path $root 'THIRD_PARTY_NOTICES.md')+';.'),
  '--add-data',($manifest+';.'),'--add-data',((Join-Path $agent 'netfx.config')+';.'))
foreach($name in @('desktop.html','desktop.ico','desktop-light.ico','desktop-dark.ico','assets','web','uia_reader.ps1','manage_frozen.ps1','uninstall.ps1','legacy_tasks.ps1')){
  $arguments+=@('--add-data',((Join-Path $agent $name)+';.'))
  if($name -in @('assets','web')){$arguments[$arguments.Count-1]=(Join-Path $agent $name)+';'+$name}
}
$arguments+=Join-Path $agent 'remote_main.py'
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
