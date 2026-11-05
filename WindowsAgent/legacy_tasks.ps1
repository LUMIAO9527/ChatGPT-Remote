[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$LegacyRoot,[ValidateSet('Inspect','Remove','Restore')][string]$Action='Inspect',[string]$Snapshot='')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$root=[IO.Path]::GetFullPath($LegacyRoot).TrimEnd('\')
$folder=Join-Path $root 'WindowsAgent'
if(!(Test-Path -LiteralPath (Join-Path $folder 'background_agent.py') -PathType Leaf) -or !(Test-Path -LiteralPath (Join-Path $folder 'desktop_app.py') -PathType Leaf)){throw 'The selected folder is not a Remote source installation.'}
$definitions=@(@{Name='ChatGPTHarmonyRemote-Agent';Script='background_agent.py'},@{Name='ChatGPTRemote-Tray';Script='desktop_app.py'})
$owned=@()
foreach($definition in $definitions){
  $task=Get-ScheduledTask -TaskName $definition.Name -ErrorAction SilentlyContinue
  if(!$task){continue}
  $actions=@($task.Actions)
  if($actions.Count -ne 1 -or $actions[0].WorkingDirectory -ne $folder -or [IO.Path]::GetFileName($actions[0].Execute) -notin @('python.exe','pythonw.exe') -or $actions[0].Arguments -notlike ('*-B "'+(Join-Path $folder $definition.Script)+'"*')){throw 'The existing task belongs to another installation.'}
  if($task.State -eq 'Running'){throw 'Stop the old Remote background service and exit its tray before importing.'}
  $owned+=@{name=$definition.Name;xml=(Export-ScheduledTask -TaskName $definition.Name)}
}
$running=@(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object {$_.CommandLine -and $_.CommandLine.IndexOf($folder,[StringComparison]::OrdinalIgnoreCase) -ge 0})
if($running.Count){throw 'Exit the old Remote program before importing.'}
if($Action -eq 'Remove'){
  if(!$Snapshot -or (Test-Path -LiteralPath $Snapshot)){throw 'A new snapshot path is required.'}
  [IO.File]::WriteAllText($Snapshot,(@{root=$root;tasks=@($owned)}|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
  foreach($entry in $owned){Unregister-ScheduledTask -TaskName $entry.name -Confirm:$false}
}elseif($Action -eq 'Restore'){
  $saved=Get-Content -LiteralPath $Snapshot -Raw -Encoding UTF8 | ConvertFrom-Json
  if($saved.root -ne $root){throw 'The snapshot belongs to another installation.'}
  foreach($entry in $saved.tasks){
    if($entry.name -notin @($definitions.Name)){throw 'Invalid snapshot task.'}
    if(Get-ScheduledTask -TaskName $entry.name -ErrorAction SilentlyContinue){continue}
    Register-ScheduledTask -TaskName $entry.name -Xml $entry.xml | Out-Null
  }
}
@{ok=$true;tasks=$owned.Count}|ConvertTo-Json -Compress
