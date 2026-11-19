#requires -Version 7.0
[CmdletBinding()]
param([Parameter(Mandatory)][ValidatePattern('^pr08b-[a-zA-Z0-9-]+$')][string]$RunId)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$brokerRoot = 'J:\class-assistant\worktrees\web-pr03b-sessions'
$broker = Join-Path $brokerRoot 'work/pr03b/vmware-broker'
$destination = Join-Path $root "outputs/pr08b/$RunId"
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$receipt = Get-Content -LiteralPath "$broker/done/$RunId.result.json" -Raw | ConvertFrom-Json
Copy-Item -LiteralPath "$broker/done/$RunId.result.json" -Destination "$destination/broker-result.json"
Copy-Item -LiteralPath $receipt.Result.StdOutPath -Destination "$destination/guest-stdout.txt"
Copy-Item -LiteralPath $receipt.Result.StdErrPath -Destination "$destination/guest-stderr.txt"
$prequeued = "$broker/done/$RunId-evidence.result.json"
$deadline = (Get-Date).AddMinutes(10)
while (-not (Test-Path -LiteralPath $prequeued) -and (Test-Path -LiteralPath "$broker/queue/$RunId-evidence.json")) {
    if ((Get-Date) -gt $deadline) { throw 'The prequeued evidence copy is still pending' }
    Start-Sleep -Seconds 2
}
$copied = if (Test-Path -LiteralPath $prequeued) { Get-Content -LiteralPath $prequeued -Raw | ConvertFrom-Json } else { $null }
if (-not $copied.Result.Succeeded) {
    $job = "$RunId-copy-" + [Guid]::NewGuid().ToString('N').Substring(0, 6)
    & "$brokerRoot/tools/pr03b/vmware/submit.ps1" -Id $job -Kind copyFrom -GuestPath "C:\j\class-assistant\worktrees\pr08b-runs\$RunId\evidence.zip" -TimeoutSeconds 120
    $copied = Get-Content -LiteralPath "$broker/done/$job.result.json" -Raw | ConvertFrom-Json
}
if (-not $copied.Result.Succeeded) { throw 'Evidence copy failed' }
Copy-Item -LiteralPath $copied.Result.HostPath -Destination "$destination/evidence.zip"
Copy-Item -LiteralPath "$broker/$RunId/SOURCE.json", "$broker/$RunId/source.zip" -Destination $destination
$source = Get-Content -LiteralPath "$destination/SOURCE.json" -Raw | ConvertFrom-Json
if ((Get-FileHash -LiteralPath "$destination/source.zip").Hash.ToLowerInvariant() -ne $source.zip_sha256) { throw 'Collected source archive hash mismatch' }
$reported = [regex]::Match((Get-Content -LiteralPath "$destination/guest-stdout.txt" -Raw), 'evidence=.* sha256=([a-f0-9]{64}) exit=')
if (-not $reported.Success -or (Get-FileHash -LiteralPath "$destination/evidence.zip").Hash.ToLowerInvariant() -ne $reported.Groups[1].Value) { throw 'Collected evidence archive hash mismatch' }
if (-not (Test-Path -LiteralPath "$destination/evidence")) { Expand-Archive -LiteralPath "$destination/evidence.zip" -DestinationPath "$destination/evidence" }
Get-FileHash -LiteralPath "$destination/source.zip", "$destination/evidence.zip" -Algorithm SHA256
