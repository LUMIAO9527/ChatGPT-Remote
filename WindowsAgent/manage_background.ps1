# Manage the Agent in the signed-in user's interactive desktop session.
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Start', 'Stop', 'Status', 'Remove', 'AutoStart', 'Recovery')]
    [string]$Action = 'Status',
    [string]$PythonPath = '',
    [ValidateSet(0,1)][int]$Enabled = 1,
    [switch]$Json
)
$ErrorActionPreference = 'Stop'
$taskName = 'ChatGPTHarmonyRemote-Agent'
$agentFolder = $PSScriptRoot
$agentScript = Join-Path $agentFolder 'background_agent.py'
$trayTaskName = 'ChatGPTRemote-Tray'
$trayScript = Join-Path $agentFolder 'desktop_app.py'

function Get-TrayTask {
    $task = Get-ScheduledTask -TaskName $trayTaskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        $actions = @($task.Actions)
        if ($actions.Count -ne 1 -or $actions[0].WorkingDirectory -ne $agentFolder -or
            $actions[0].Arguments -notlike ('*"' + $trayScript + '"*')) {
            throw 'The tray task belongs to a different configuration; it was not changed.'
        }
    }
    return $task
}

function Set-TrayAutostart([bool]$enable, [string]$interpreter) {
    $existing = Get-TrayTask
    if ($null -eq $existing) {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $launch = New-ScheduledTaskAction -Execute $interpreter -WorkingDirectory $agentFolder `
            -Argument ('-B "' + $trayScript + '" --background')
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
        $trigger.Enabled = $enable
        $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -Hidden `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
        Register-ScheduledTask -TaskName $trayTaskName -Action $launch -Trigger $trigger `
            -Principal $principal -Settings $settings -Description 'ChatGPT Remote notification-area manager.' | Out-Null
    } else {
        $triggers = @($existing.Triggers)
        foreach ($trigger in $triggers) { $trigger.Enabled = $enable }
        Set-ScheduledTask -TaskName $trayTaskName -Trigger $triggers | Out-Null
    }
}

function Get-AgentTask {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        $actions = @($task.Actions)
        if ($actions.Count -ne 1 -or $actions[0].WorkingDirectory -ne $agentFolder -or
            $actions[0].Arguments -notlike ('*"' + $agentScript + '"*')) {
            throw 'The task name belongs to a different configuration; it was not changed.'
        }
    }
    return $task
}

function Show-AgentTask {
    $task = Get-AgentTask
    if ($null -eq $task) {
        $value = [pscustomobject]@{TaskName=$taskName; Installed=$false; AutoStart=$false}
        if ($Json) { $value | ConvertTo-Json -Compress } else { $value }
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    $tray = Get-TrayTask
    $logon = @($task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' })
    $value = [pscustomobject]@{
        TaskName=$taskName; Installed=$true; State=[string]$task.State
        AutoStart=($logon.Count -gt 0 -and $logon[0].Enabled)
        TrayInstalled=($null -ne $tray)
        LastRunTime=$info.LastRunTime; LastTaskResult=$info.LastTaskResult
        NextRunTime=$info.NextRunTime; User=$task.Principal.UserId
        LogonType=[string]$task.Principal.LogonType
        MultipleInstances=[string]$task.Settings.MultipleInstances
        ExecutionTimeLimit=$task.Settings.ExecutionTimeLimit
    }
    if ($Json) { $value | ConvertTo-Json -Compress } else { $value }
}

function Set-AgentTriggers($task, [bool]$auto, [bool]$recover) {
    $triggers = @($task.Triggers)
    foreach ($trigger in $triggers) {
        if ($trigger.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger') { $trigger.Enabled = $auto }
        elseif ($trigger.CimClass.CimClassName -eq 'MSFT_TaskTimeTrigger') { $trigger.Enabled = $recover }
    }
    Set-ScheduledTask -TaskName $taskName -Trigger $triggers | Out-Null
}

if ($Action -eq 'Status') { Show-AgentTask; return }
$digest = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($agentFolder.ToLowerInvariant()))).Substring(0,24)
$mutex = [Threading.Mutex]::new($false, ('Local\ChatGPTRemote.Task.' + $digest))
$held = $false
try {
try { $held = $mutex.WaitOne(15000) } catch [Threading.AbandonedMutexException] { $held = $true }
if (!$held) { throw 'Another service operation is still running.' }
if ($Action -eq 'Install') {
    $existing = Get-AgentTask
    if ($null -ne $existing) { throw 'The background task is already installed; use Start instead.' }
    if ([string]::IsNullOrWhiteSpace($PythonPath)) {
        $venv = Join-Path $agentFolder '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $venv) { $PythonPath = $venv }
        else {
            $PythonPath = (& py -3 -B -c 'import sys; print(sys.executable)').Trim()
            if ($LASTEXITCODE -ne 0) { throw 'Python 3 is unavailable.' }
        }
    }
    $PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
    $windowlessPython = Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
    if (!(Test-Path -LiteralPath $windowlessPython)) { throw 'pythonw.exe is unavailable beside Python.' }
    $projectLibraries = Join-Path (Split-Path -Parent $agentFolder) '_wip\desktop-runtime\libs'
    & $PythonPath -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import PIL; import ctypes' $projectLibraries
    if ($LASTEXITCODE -ne 0) { throw 'The selected Python is missing the Agent dependencies.' }
    $listeners = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0 -and ($null -eq $existing -or $existing.State -ne 'Running')) {
        throw 'Port 8765 is already in use; the existing process was not stopped.'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
    $launch = New-ScheduledTaskAction -Execute $windowlessPython -WorkingDirectory $agentFolder -Argument (
        '-B "' + $agentScript + '" --bind 127.0.0.1 --profile remote --trusted-host auto --access-key auto')
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
    # Repeating triggers are ignored while the single long-running instance is healthy.
    $recovery = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -Hidden `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $definition = New-ScheduledTask -Action $launch -Trigger @($logon,$recovery) -Principal $principal -Settings $settings `
        -Description 'Run ChatGPT Remote in the signed-in desktop session; resume service after an unexpected exit.'
    Register-ScheduledTask -TaskName $taskName -InputObject $definition -Force | Out-Null
    Set-TrayAutostart $true $windowlessPython
    if ((Get-AgentTask).State -ne 'Running') { Start-ScheduledTask -TaskName $taskName }
} elseif ($Action -eq 'Start') {
    $task = Get-AgentTask
    if ($null -eq $task) { throw 'The background task is not installed.' }
    $auto = @($task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and $_.Enabled }).Count -gt 0
    Set-AgentTriggers $task $auto $auto
    Enable-ScheduledTask -TaskName $taskName | Out-Null
    if ((Get-AgentTask).State -ne 'Running') { Start-ScheduledTask -TaskName $taskName }
} elseif ($Action -eq 'Stop') {
    $task = Get-AgentTask
    if ($null -eq $task) { throw 'The background task is not installed.' }
    $auto = @($task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and $_.Enabled }).Count -gt 0
    Set-AgentTriggers $task $auto $false
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
} elseif ($Action -eq 'AutoStart') {
    $task = Get-AgentTask
    if ($null -eq $task) { throw 'The background task is not installed.' }
    Set-TrayAutostart ([bool]$Enabled) ([string]$task.Actions[0].Execute)
    Set-AgentTriggers $task ([bool]$Enabled) ([bool]$Enabled -and $task.State -eq 'Running')
    Enable-ScheduledTask -TaskName $taskName | Out-Null
} elseif ($Action -eq 'Recovery') {
    $task = Get-AgentTask
    if ($null -ne $task -and $task.State -eq 'Running') {
        $auto = @($task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and $_.Enabled }).Count -gt 0
        if ($auto) { Set-AgentTriggers $task $true $true }
    }
} elseif ($Action -eq 'Remove') {
    $tray = Get-TrayTask
    if ($null -ne $tray) {
        Stop-ScheduledTask -TaskName $trayTaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $trayTaskName -Confirm:$false
    }
    if ($null -ne (Get-AgentTask)) {
        Disable-ScheduledTask -TaskName $taskName | Out-Null
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
}
Show-AgentTask
} finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
