#requires -Version 7.0
<#
.SYNOPSIS
  Host-side job broker for running PR-03b tests inside the VMware guest.

.DESCRIPTION
  Started by the user in their own terminal. Prompts once for the VM encryption
  password and the guest Windows credential, keeps both in this process's memory
  only, and then executes jobs dropped into work/pr03b/vmware-broker/queue using
  the existing D:\Codex\Projects\vmware-cli module. Nothing is persisted.

  The broker never stops, restarts, suspends or snapshots the VM. It starts the
  VM only when -StartVM is given. It shares this worktree with the guest
  read-only unless -SkipShare is given.

  Job file (queue\<id>.json, written as .tmp and renamed):
    { "id": "...", "kind": "run",      "script": "<host .ps1>", "timeoutSeconds": 600 }
    { "id": "...", "kind": "copyFrom", "guestPath": "C:\\...\\file.zip" }
    { "id": "...", "kind": "copyTo",   "hostPath": "<file in worktree>", "guestPath": "C:\\..." }
    { "id": "...", "kind": "status" }
  Result: done\<id>.result.json. Stop the broker with Ctrl+C or by creating
  work/pr03b/vmware-broker/stop.
#>
[CmdletBinding()]
param(
    [string]$ModulePath = 'D:\Codex\Projects\vmware-cli\VMware.psm1',
    [string]$VmxPath,
    [string]$ShareName = 'pr03b-src',
    [switch]$StartVM,
    [switch]$SkipShare,
    [ValidateRange(1, 60)][int]$PollSeconds = 2
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
$base = Join-Path $repo 'work\pr03b\vmware-broker'
$dirs = @{}
foreach ($name in 'queue', 'done', 'runs', 'scripts', 'incoming') {
    $dirs[$name] = Join-Path $base $name
    $null = New-Item -ItemType Directory -Path $dirs[$name] -Force
}
$stopFile = Join-Path $base 'stop'
$statusFile = Join-Path $base 'broker.status.json'
if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile -Force }

function Write-Json([string]$Path, $Value) {
    $tmp = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $tmp -Encoding utf8
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Get-Prop($Object, [string]$Name) {
    $p = $Object.PSObject.Properties[$Name]
    if ($p) { $p.Value } else { $null }
}
function Test-Under([string]$Path, [string]$Root) {
    $full = [IO.Path]::GetFullPath($Path)
    $full.StartsWith($Root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
}
function Say([string]$Text) { Write-Host ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $Text) }

Import-Module $ModulePath -Force
$environment = Get-VMwareEnvironment
if (-not $VmxPath) {
    $VmxPath = (Get-Content -LiteralPath (Join-Path (Split-Path $ModulePath) 'config.json') -Raw | ConvertFrom-Json).DefaultVmx
}
$VmxPath = (Resolve-Path -LiteralPath $VmxPath).Path
Say "Target VM: $VmxPath"
Say "Worktree:  $repo"

$vmPassword = Read-Host 'VM encryption password (press Enter if the VM is not encrypted)' -AsSecureString
if ($vmPassword.Length -eq 0) { $vmPassword = $null }
$guestCredential = Get-Credential -Message 'Guest Windows account (use the password, not a Windows Hello PIN)'
if (-not $guestCredential) { throw 'Guest credential is required.' }

$common = @{ VmxPath = $VmxPath }
if ($vmPassword) { $common.VMPassword = $vmPassword }

# Shared-folder commands are not exposed by the module, so call vmrun directly
# with the same separated-argument, redacted approach.
function Invoke-Vmrun([string[]]$Arguments, [int]$TimeoutSeconds = 60) {
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $environment.VmrunPath
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $plain = $null
    $psi.ArgumentList.Add('-T'); $psi.ArgumentList.Add('ws')
    if ($vmPassword) {
        $plain = [Net.NetworkCredential]::new('', $vmPassword).Password
        $psi.ArgumentList.Add('-vp'); $psi.ArgumentList.Add($plain)
    }
    foreach ($a in $Arguments) { $psi.ArgumentList.Add($a) }
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $psi
    try {
        $null = $process.Start()
        $out = $process.StandardOutput.ReadToEndAsync(); $err = $process.StandardError.ReadToEndAsync()
        $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
        if ($timedOut) { $process.Kill($true); $process.WaitForExit() }
        $text = ($out.GetAwaiter().GetResult() + ' ' + $err.GetAwaiter().GetResult()).Trim()
        if ($plain) { $text = $text.Replace($plain, '[REDACTED]') }
        [pscustomobject]@{ ExitCode = $process.ExitCode; TimedOut = $timedOut; Text = $text; Succeeded = (-not $timedOut -and $process.ExitCode -eq 0) }
    } finally { $plain = $null; $process.Dispose() }
}

$running = @($environment.VirtualMachines | Where-Object { $_.Running -and $_.VmxPath -ieq $VmxPath }).Count -gt 0
if (-not $running) {
    if (-not $StartVM) { throw 'The VM is not running. Start it in VMware Workstation, or rerun this script with -StartVM.' }
    Say 'Starting the VM (requested with -StartVM)...'
    $null = Invoke-VMware Start @common -TimeoutSeconds 300 -Confirm:$false
}
Say 'Waiting for VMware Tools...'
$deadline = (Get-Date).AddMinutes(8); $toolsReady = $false
while ((Get-Date) -lt $deadline) {
    try { if ((Invoke-VMware Tools @common -TimeoutSeconds 30).ToolsAvailable) { $toolsReady = $true; break } } catch { }
    Start-Sleep -Seconds 5
}
if (-not $toolsReady) { throw 'VMware Tools did not report running within 8 minutes.' }

$share = [ordered]@{ Requested = (-not $SkipShare); Name = $ShareName; HostPath = $repo; Mode = 'readonly'; Ready = $false; Detail = '' }
if (-not $SkipShare) {
    $enable = Invoke-Vmrun @('enableSharedFolders', $VmxPath)
    $add = Invoke-Vmrun @('addSharedFolder', $VmxPath, $ShareName, $repo)   # fails harmlessly if it already exists
    $state = Invoke-Vmrun @('setSharedFolderState', $VmxPath, $ShareName, $repo, 'readonly')
    $share.Ready = $enable.Succeeded -and $state.Succeeded
    $share.Detail = "enable=$($enable.ExitCode) add=$($add.ExitCode) state=$($state.ExitCode) $($state.Text)".Trim()
    Say ("Shared folder '{0}' read-only: {1}" -f $ShareName, $(if ($share.Ready) { 'ready' } else { "NOT ready ($($share.Detail))" }))
}

function Invoke-Job($Job) {
    $kind = [string](Get-Prop $Job 'kind')
    $timeout = [int](Get-Prop $Job 'timeoutSeconds'); if ($timeout -lt 1) { $timeout = 600 }; if ($timeout -gt 3600) { $timeout = 3600 }
    switch ($kind) {
        'run' {
            $script = [IO.Path]::GetFullPath([string](Get-Prop $Job 'script'))
            if (-not ((Test-Under $script $dirs.scripts) -or (Test-Under $script (Join-Path $repo 'tools\pr03b\vmware')))) { throw 'Job script must live in the broker scripts folder or tools/pr03b/vmware.' }
            if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw 'Job script does not exist.' }
            Say "  run $script (timeout ${timeout}s)"
            Invoke-VMware Run @common -GuestCredential $guestCredential -ScriptPath $script -OutputDirectory $dirs.runs -TimeoutSeconds $timeout -Confirm:$false
        }
        'copyFrom' {
            $guestPath = [string](Get-Prop $Job 'guestPath')
            if (-not $guestPath) { throw 'guestPath is required.' }
            $target = Join-Path $dirs.incoming ([string]$Job.id)
            $null = New-Item -ItemType Directory -Path $target -Force
            $destination = Join-Path $target ([IO.Path]::GetFileName($guestPath))
            Say "  copyFrom $guestPath"
            $r = Invoke-VMware CopyFromGuest @common -GuestCredential $guestCredential -Source $guestPath -Destination $destination -TimeoutSeconds $timeout -Confirm:$false
            $r | Add-Member -NotePropertyName HostPath -NotePropertyValue $destination -PassThru
        }
        'copyTo' {
            $hostPath = [IO.Path]::GetFullPath([string](Get-Prop $Job 'hostPath'))
            if (-not (Test-Under $hostPath $repo)) { throw 'hostPath must be inside the worktree.' }
            $guestPath = [string](Get-Prop $Job 'guestPath')
            if (-not $guestPath) { throw 'guestPath is required.' }
            Say "  copyTo $hostPath -> $guestPath"
            Invoke-VMware CopyToGuest @common -GuestCredential $guestCredential -Source $hostPath -Destination $guestPath -TimeoutSeconds $timeout -Confirm:$false
        }
        'status' {
            $tools = Invoke-VMware Tools @common -TimeoutSeconds 30
            $ip = try { (Invoke-VMware IP @common -TimeoutSeconds 60).StdOut } catch { '' }
            [pscustomobject]@{ Succeeded = $true; ToolsState = $tools.ToolsState; GuestIP = $ip; Share = $share }
        }
        default { throw "Unknown job kind '$kind'." }
    }
}

$started = [DateTime]::UtcNow
Say "Broker ready. Queue: $($dirs.queue). Press Ctrl+C or create '$stopFile' to stop. The VM is left running."
try {
    while (-not (Test-Path -LiteralPath $stopFile)) {
        Write-Json $statusFile ([ordered]@{ Pid = $PID; StartedUtc = $started.ToString('o'); HeartbeatUtc = [DateTime]::UtcNow.ToString('o'); VmxPath = $VmxPath; Share = $share; Busy = $null })
        foreach ($file in @(Get-ChildItem -LiteralPath $dirs.queue -Filter '*.json' -File | Sort-Object LastWriteTimeUtc)) {
            $begin = [DateTime]::UtcNow; $id = $file.BaseName; $result = $null; $failure = $null
            try {
                $job = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
                if ([string](Get-Prop $job 'id') -ne $id -or $id -notmatch '^[A-Za-z0-9_-]{1,64}$') { throw 'Job id must match its file name and be [A-Za-z0-9_-]{1,64}.' }
                Say "Job $id ($([string](Get-Prop $job 'kind')))"
                Write-Json $statusFile ([ordered]@{ Pid = $PID; StartedUtc = $started.ToString('o'); HeartbeatUtc = $begin.ToString('o'); VmxPath = $VmxPath; Share = $share; Busy = $id })
                $result = Invoke-Job $job
            } catch { $failure = $_.Exception.Message }
            $record = [ordered]@{ Id = $id; BeganUtc = $begin.ToString('o'); EndedUtc = [DateTime]::UtcNow.ToString('o'); BrokerError = $failure; Result = $result }
            Write-Json (Join-Path $dirs.done "$id.result.json") $record
            Move-Item -LiteralPath $file.FullName -Destination (Join-Path $dirs.done "$id.job.json") -Force
            Say ("  done: {0}" -f $(if ($failure) { "BROKER ERROR $failure" } else { 'ok' }))
        }
        Start-Sleep -Seconds $PollSeconds
    }
} finally {
    Write-Json $statusFile ([ordered]@{ Pid = $PID; StartedUtc = $started.ToString('o'); StoppedUtc = [DateTime]::UtcNow.ToString('o'); VmxPath = $VmxPath; Share = $share; Busy = $null })
    $vmPassword = $null; $guestCredential = $null; $common.Clear()
    Say 'Broker stopped. Credentials released; VM state unchanged.'
}
