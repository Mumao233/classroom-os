#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('targeted', 'frontend', 'full', 'legacy')][string]$Mode = 'targeted',
    [ValidatePattern('^pr08b-[a-zA-Z0-9-]+$')][string]$RunId = ('pr08b-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)),
    [int]$TimeoutSeconds = 2400
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$brokerRoot = 'J:\class-assistant\worktrees\web-pr03b-sessions'
$broker = Join-Path $brokerRoot 'work/pr03b/vmware-broker'
$payload = Join-Path $broker $RunId
if (Test-Path -LiteralPath $payload) { throw 'Run id already used' }
& python -B (Join-Path $root 'tools/pr04b/prepare_payload.py') $payload 'J:\class-assistant\worktrees\web-pr07b-summary-versions\work\pr07b\validation-wheels'
if ($LASTEXITCODE -ne 0) { throw 'Source preparation failed' }
$hash = (Get-FileHash -LiteralPath "$payload/source.zip").Hash.ToLowerInvariant()
$script = Join-Path $broker "scripts/$RunId.ps1"
$body = "`$SnapshotTag = '$RunId'`r`n`$TestMode = '$Mode'`r`n`$SourceHash = '$hash'`r`n" + (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'guest.ps1') -Raw)
Set-Content -LiteralPath $script -Value $body -Encoding utf8
$destination = Join-Path $root "outputs/pr08b/$RunId"
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Copy-Item -LiteralPath "$payload/SOURCE.json" -Destination "$destination/SOURCE.json"
Write-Output "run=$RunId mode=$Mode source_sha256=$hash"
$submission = Start-ThreadJob -ScriptBlock {
    param($BrokerRoot, $RunId, $Script, $TimeoutSeconds)
    & "$BrokerRoot/tools/pr03b/vmware/submit.ps1" -Id $RunId -Script $Script -TimeoutSeconds $TimeoutSeconds -TailLines 120
} -ArgumentList $brokerRoot, $RunId, $script, $TimeoutSeconds
try {
    $deadline = (Get-Date).AddSeconds(15)
    while (-not (Test-Path -LiteralPath "$broker/queue/$RunId.json") -and -not (Test-Path -LiteralPath "$broker/done/$RunId.result.json")) {
        if ($submission.State -in 'Completed', 'Failed', 'Stopped' -or (Get-Date) -gt $deadline) {
            Receive-Job -Job $submission
            throw 'Broker submission did not create a job'
        }
        Start-Sleep -Milliseconds 100
    }
    # Reserve the evidence copy immediately behind this run in the existing serial queue.
    $copyId = "$RunId-evidence"
    $copy = @{ id = $copyId; kind = 'copyFrom'; guestPath = "C:\j\class-assistant\worktrees\pr08b-runs\$RunId\evidence.zip"; timeoutSeconds = 120 }
    $copy | ConvertTo-Json | Set-Content -LiteralPath "$broker/queue/$copyId.tmp" -Encoding utf8
    Move-Item -LiteralPath "$broker/queue/$copyId.tmp" -Destination "$broker/queue/$copyId.json"
    Receive-Job -Job $submission -Wait
} finally {
    Remove-Job -Job $submission -Force
}
$receipt = Get-Content -LiteralPath "$broker/done/$RunId.result.json" -Raw | ConvertFrom-Json
$code = if ($receipt.BrokerError) { 2 } else { [int]$receipt.Result.GuestExitCode }
Copy-Item -LiteralPath "$broker/done/$RunId.result.json" -Destination "$destination/broker-result.json"
exit $code
