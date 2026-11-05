[CmdletBinding()]
param([string]$PythonPath='', [switch]$NativeWorkers)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
if(!$PythonPath){$PythonPath=(& py -3 -B -c 'import sys; print(sys.executable)').Trim()}
$temporary=Join-Path $root '_wip\checks\temp'
New-Item -ItemType Directory -Path $temporary -Force | Out-Null
$values=@{TEMP=$temporary;TMP=$temporary;PYTHONDONTWRITEBYTECODE='1';PYTHONIOENCODING='utf-8'}
$previous=@{}
foreach($key in $values.Keys){$previous[$key]=[Environment]::GetEnvironmentVariable($key,'Process');[Environment]::SetEnvironmentVariable($key,$values[$key],'Process')}
Push-Location $root
try{
  & $PythonPath -B -m unittest discover -s WindowsAgent/tests -p 'test_*.py'
  if($LASTEXITCODE){throw 'Python regression failed.'}
  node --test WindowsAgent/tests/test_web.mjs WindowsAgent/tests/test_desktop_web.mjs
  if($LASTEXITCODE){throw 'Web regression failed.'}
  if($NativeWorkers){
    & $PythonPath -B WindowsAgent/tests/verify_lifecycle_windows.py
    if($LASTEXITCODE){throw 'Native lifecycle verification failed.'}
  }
}finally{
  Pop-Location
  foreach($key in $previous.Keys){[Environment]::SetEnvironmentVariable($key,$previous[$key],'Process')}
}
