#requires -Version 7.0
<# Host-side helper: queue one broker job and wait for its result. Holds no credentials. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Id,
    [ValidateSet('run', 'copyFrom', 'copyTo', 'status')][string]$Kind = 'run',
    [string]$Script, [string]$GuestPath, [string]$HostPath,
    [int]$TimeoutSeconds = 600, [int]$TailLines = 60
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
$base = Join-Path $repo 'work\pr03b\vmware-broker'
$status = Get-Content -LiteralPath (Join-Path $base 'broker.status.json') -Raw | ConvertFrom-Json
if ($status.PSObject.Properties['StoppedUtc'] -or -not (Get-Process -Id $status.Pid -ErrorAction SilentlyContinue)) { throw 'Broker is not running.' }
$result = Join-Path $base "done\$Id.result.json"
if (Test-Path -LiteralPath $result) { throw "Job id '$Id' was already used." }
$job = [ordered]@{ id = $Id; kind = $Kind; timeoutSeconds = $TimeoutSeconds }
if ($Script) { $job.script = (Resolve-Path -LiteralPath $Script).Path }
if ($GuestPath) { $job.guestPath = $GuestPath }
if ($HostPath) { $job.hostPath = (Resolve-Path -LiteralPath $HostPath).Path }
$file = Join-Path $base "queue\$Id.json"
$job | ConvertTo-Json | Set-Content -LiteralPath "$file.tmp" -Encoding utf8
Move-Item -LiteralPath "$file.tmp" -Destination $file
$deadline = (Get-Date).AddSeconds($TimeoutSeconds * 4 + 120)
while (-not (Test-Path -LiteralPath $result)) {
    if ((Get-Date) -gt $deadline) { throw 'Timed out waiting for the broker result; the guest job may still be running.' }
    Start-Sleep -Seconds 2
}
$record = Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
"began=$($record.BeganUtc) ended=$($record.EndedUtc)"
if ($record.BrokerError) { "BROKER ERROR: $($record.BrokerError)"; exit 2 }
if ($Kind -eq 'run') {
    "transport=$($record.Result.TransportSucceeded) guest_exit=$($record.Result.GuestExitCode) output=$($record.Result.OutputDirectory)"
    Get-Content -LiteralPath $record.Result.StdOutPath -Encoding utf8 -Tail $TailLines
    $err = @(Get-Content -LiteralPath $record.Result.StdErrPath -Encoding utf8 | Where-Object { $_ -and $_ -notmatch '^#< CLIXML|^<Objs ' })
    if ($err.Count) { '--- stderr (tail)'; $err | Select-Object -Last 20 }
    exit ([int]$record.Result.GuestExitCode)
}
$record.Result | ConvertTo-Json -Depth 5
