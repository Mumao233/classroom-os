#requires -Version 7.0
[CmdletBinding()]
param([Parameter(Mandatory)][ValidatePattern('^pr08a-[a-zA-Z0-9-]+$')][string]$RunId)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$brokerRoot = 'J:\class-assistant\worktrees\web-pr03b-sessions'
$broker = Join-Path $brokerRoot 'work\pr03b\vmware-broker'
$payload = Join-Path $broker $RunId
if (Test-Path -LiteralPath $payload) { throw 'Run id already used' }
& python -B (Join-Path $root 'tools\pr04b\prepare_payload.py') $payload 'J:\class-assistant\worktrees\web-pr07b-summary-versions\work\pr07b\validation-wheels'
if ($LASTEXITCODE -ne 0) { throw 'Preparation failed' }
$hash = (Get-FileHash -LiteralPath "$payload\source.zip").Hash.ToLowerInvariant()
$script = Join-Path $broker "scripts\$RunId.ps1"
$text = "`$SnapshotTag = '$RunId'`r`n`$SourceHash = '$hash'`r`n" + (Get-Content -LiteralPath "$PSScriptRoot\guest.ps1" -Raw)
Set-Content -LiteralPath $script -Value $text -Encoding utf8
New-Item -ItemType Directory -Path (Join-Path $root "outputs\pr08a\$RunId") -Force | Out-Null
Copy-Item -LiteralPath "$payload\SOURCE.json" -Destination (Join-Path $root "outputs\pr08a\$RunId\SOURCE.json")
& "$brokerRoot\tools\pr03b\vmware\submit.ps1" -Id $RunId -Script $script -TimeoutSeconds 1200 -TailLines 100
exit $LASTEXITCODE
