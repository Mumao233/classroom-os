#requires -Version 7.0
[CmdletBinding()]
param([Parameter(Mandatory)][string]$Tag)
$ErrorActionPreference = 'Stop'
if ($Tag -notmatch '^pr05b-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$') { throw 'Invalid run tag' }
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$brokerRoot = 'J:\class-assistant\worktrees\web-pr03b-sessions'
$broker = Join-Path $brokerRoot 'work/pr03b/vmware-broker'
$destination = Join-Path $root "outputs/pr05b/runs/$Tag"
$null = New-Item -ItemType Directory -Force -Path $destination
$prior = Get-ChildItem -LiteralPath (Join-Path $broker 'done') -Filter "$Tag-collect-*.result.json" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$copied = if ($prior) { Get-Content -LiteralPath $prior.FullName -Raw | ConvertFrom-Json } else { $null }
if (-not $copied.Result.Succeeded) {
    $job = "$Tag-collect-" + [Guid]::NewGuid().ToString('N').Substring(0,6)
    & (Join-Path $brokerRoot 'tools/pr03b/vmware/submit.ps1') -Id $job -Kind copyFrom -GuestPath "C:\j\class-assistant\worktrees\pr05b-runs\$Tag\evidence.zip" -TimeoutSeconds 60
    $copied = Get-Content -LiteralPath (Join-Path $broker "done/$job.result.json") -Raw | ConvertFrom-Json
}
if (-not $copied.Result.Succeeded) { throw 'Evidence copy failed' }
Copy-Item -LiteralPath $copied.Result.HostPath -Destination (Join-Path $destination 'evidence.zip')
foreach ($name in @('source.zip','SOURCE.json')) {
    Copy-Item -LiteralPath (Join-Path $broker "$Tag/$name") -Destination (Join-Path $destination $name)
}
$receipt = Get-Content -LiteralPath (Join-Path $broker "done/$Tag.result.json") -Raw | ConvertFrom-Json
Copy-Item -LiteralPath (Join-Path $broker "done/$Tag.result.json") -Destination (Join-Path $destination 'broker-result.json')
Copy-Item -LiteralPath $receipt.Result.StdOutPath -Destination (Join-Path $destination 'guest-stdout.txt')
Copy-Item -LiteralPath $receipt.Result.StdErrPath -Destination (Join-Path $destination 'guest-stderr.txt')
if (-not (Test-Path -LiteralPath (Join-Path $destination 'evidence'))) {
    Expand-Archive -LiteralPath (Join-Path $destination 'evidence.zip') -DestinationPath (Join-Path $destination 'evidence')
}
Get-FileHash -LiteralPath (Join-Path $destination 'source.zip'),(Join-Path $destination 'evidence.zip') -Algorithm SHA256
