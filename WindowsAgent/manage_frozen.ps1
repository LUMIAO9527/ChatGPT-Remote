[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$InstallRoot,
  [ValidateSet('Status','Install','Start','Stop','Remove','AutoStart','Recovery')][string]$Action='Status',
  [ValidateSet(0,1)][int]$Enabled=1,
  [string]$TaskNamespace=''
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
if($TaskNamespace -and $TaskNamespace -notmatch '^Test[a-f0-9]{32}$'){throw 'Invalid test task namespace.'}
$root=[IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
if($root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\')){throw 'Invalid installation root.'}
$executable=Join-Path $root 'ChatGPTRemote.exe'
$marker=Get-Content -LiteralPath (Join-Path $root '.chatgptremote-install.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($marker.app -ne 'lumiao.chatgpt.remote' -or $marker.schema -ne 1 -or $marker.root -ne $root -or !(Test-Path -LiteralPath $executable -PathType Leaf)){throw 'Installation ownership could not be verified.'}
$names=@{agent=('ChatGPTHarmonyRemote-Agent'+$TaskNamespace);tray=('ChatGPTRemote-Tray'+$TaskNamespace)}
function Owned([string]$role){
  $task=Get-ScheduledTask -TaskName $names[$role] -ErrorAction SilentlyContinue
  if($task){
    $actions=@($task.Actions)
    if($actions.Count -ne 1 -or [IO.Path]::GetFullPath($actions[0].Execute) -ne $executable -or $actions[0].WorkingDirectory -ne $root -or $actions[0].Arguments -notmatch ('^--'+$role+'(?:\s|$)')){throw 'Another installation owns this background task. Stop and remove its tasks before migrating.'}
  }
  return $task
}
function ShowStatus {
  $agent=Owned 'agent';$tray=Owned 'tray'
  $auto=$false
  if($agent){$auto=@($agent.Triggers|Where-Object {$_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and $_.Enabled}).Count -gt 0}
  @{Installed=($null -ne $agent);State=$(if($agent){[string]$agent.State}else{'NotInstalled'});AutoStart=$auto;TrayInstalled=($null -ne $tray)} | ConvertTo-Json -Compress
}
if($Action -eq 'Status'){ShowStatus;return}
$sha=[Security.Cryptography.SHA256]::Create()
try{$identityHash=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant()))).Replace('-','')}finally{$sha.Dispose()}
$mutex=[Threading.Mutex]::new($false,('Local\ChatGPTRemote.Task.'+$identityHash))
$held=$false
try{
  try{$held=$mutex.WaitOne(15000)}catch [Threading.AbandonedMutexException]{$held=$true}
  if(!$held){throw 'Another background operation is running.'}
  $agent=Owned 'agent';$tray=Owned 'tray'
  if($Action -eq 'Install'){
    if($agent){throw 'The background task is already installed.'}
    if(@(Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue).Count){throw 'The connection port is in use. Stop the existing service first.'}
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
    $created=@()
    try {
    foreach($role in @('agent','tray')){
      if(Owned $role){continue}
      $arguments=if($role -eq 'agent'){'--agent --bind 127.0.0.1 --profile remote --trusted-host auto --access-key auto'}else{'--tray --background'}
      $launch=New-ScheduledTaskAction -Execute $executable -WorkingDirectory $root -Argument $arguments
      $logon=New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
      $triggers=@($logon)
      if($role -eq 'agent'){$triggers+=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)}
      $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
      Register-ScheduledTask -TaskName $names[$role] -Action $launch -Trigger $triggers -Principal $principal -Settings $settings -Description 'ChatGPT Remote desktop connection.' | Out-Null
      $created+=$role
    }
    Start-ScheduledTask -TaskName $names.agent
    } catch {
      foreach($role in $created){if(Owned $role){Stop-ScheduledTask -TaskName $names[$role] -ErrorAction SilentlyContinue;Unregister-ScheduledTask -TaskName $names[$role] -Confirm:$false}}
      throw
    }
  }elseif($Action -eq 'Remove'){
    foreach($role in @('agent','tray')){if(Owned $role){Disable-ScheduledTask -TaskName $names[$role] | Out-Null;Stop-ScheduledTask -TaskName $names[$role] -ErrorAction SilentlyContinue;Unregister-ScheduledTask -TaskName $names[$role] -Confirm:$false}}
  }elseif($Action -eq 'Stop'){
    if(!$agent){throw 'The background task is not installed.'}
    $triggers=@($agent.Triggers);foreach($trigger in $triggers){if($trigger.CimClass.CimClassName -ne 'MSFT_TaskLogonTrigger'){$trigger.Enabled=$false}}
    Set-ScheduledTask -TaskName $names.agent -Trigger $triggers | Out-Null
    Stop-ScheduledTask -TaskName $names.agent -ErrorAction SilentlyContinue
  }elseif($Action -eq 'Start'){
    if(!$agent){throw 'The background task is not installed.'}
    $auto=@($agent.Triggers|Where-Object {$_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and $_.Enabled}).Count -gt 0
    $triggers=@($agent.Triggers);foreach($trigger in $triggers){if($trigger.CimClass.CimClassName -ne 'MSFT_TaskLogonTrigger'){$trigger.Enabled=$auto}}
    Set-ScheduledTask -TaskName $names.agent -Trigger $triggers | Out-Null
    Enable-ScheduledTask -TaskName $names.agent | Out-Null
    if((Owned 'agent').State -ne 'Running'){Start-ScheduledTask -TaskName $names.agent}
  }elseif($Action -in @('AutoStart','Recovery')){
    if(!$agent){throw 'The background task is not installed.'}
    $auto=if($Action -eq 'AutoStart'){[bool]$Enabled}else{@($agent.Triggers|Where-Object {$_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and $_.Enabled}).Count -gt 0}
    foreach($role in @('agent','tray')){$task=Owned $role;if(!$task){continue};$triggers=@($task.Triggers);foreach($trigger in $triggers){$trigger.Enabled=$auto -and ($trigger.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -or $task.State -eq 'Running')};Set-ScheduledTask -TaskName $names[$role] -Trigger $triggers | Out-Null}
  }
  ShowStatus
}finally{if($held){$mutex.ReleaseMutex()};$mutex.Dispose()}
