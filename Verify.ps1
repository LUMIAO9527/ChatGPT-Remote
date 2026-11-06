[CmdletBinding()]
param([string]$PythonPath='', [string]$DependencyPath='', [switch]$NativeWorkers)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
if(!$PythonPath){$PythonPath=(& py -3 -B -c 'import sys; print(sys.executable)').Trim()}
$temporary=Join-Path $root '_wip\checks\temp'
New-Item -ItemType Directory -Path $temporary -Force | Out-Null
$values=@{TEMP=$temporary;TMP=$temporary;PYTHONDONTWRITEBYTECODE='1';PYTHONIOENCODING='utf-8'}
if(!$DependencyPath){$DependencyPath=Join-Path $root '_wip\build\libs'}
if(Test-Path -LiteralPath $DependencyPath){$values['PYTHONPATH']=$DependencyPath}
$previous=@{}
foreach($key in $values.Keys){$previous[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,$values[$key],'Process')}
Push-Location $root
try{
  & $PythonPath -B -m unittest discover -s WindowsAgent/tests -p 'test_*.py'
  if($LASTEXITCODE){throw 'Python regression failed.'}
  $webTests=@(Get-ChildItem -LiteralPath (Join-Path $root 'WindowsAgent/tests') -Filter 'test_*.mjs' | Sort-Object Name | ForEach-Object FullName)
  node --test @webTests
  if($LASTEXITCODE){throw 'Web regression failed.'}
  if($NativeWorkers){
    & $PythonPath -B WindowsAgent/tests/verify_lifecycle_windows.py
    if($LASTEXITCODE){throw 'Native lifecycle verification failed.'}
  }
}finally{
  Pop-Location
  foreach($key in $previous.Keys){[Environment]::SetEnvironmentVariable($key,$previous[$key],'Process')}
}
