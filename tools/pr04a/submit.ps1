#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('targeted','full','r1')][string]$Mode = 'targeted',
    [string]$Python = 'C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe',
    [string]$BrokerRoot = 'J:\class-assistant\worktrees\web-pr03b-sessions'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$tag = 'pr04a-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
& $Python -B (Join-Path $PSScriptRoot 'prepare_payload.py')
if ($LASTEXITCODE -ne 0) { throw 'source preparation failed' }
$broker = Join-Path $BrokerRoot 'work\pr03b\vmware-broker'
$payload = Join-Path $broker $tag
$null = New-Item -ItemType Directory -Path $payload
Copy-Item -LiteralPath (Join-Path $repo 'work\pr04a\payload.zip') -Destination (Join-Path $payload 'payload.zip')
$script = Join-Path $broker "scripts\$tag.ps1"
$body = "`$SnapshotTag = '$tag'`r`n`$TestMode = '$Mode'`r`n" + (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'guest.ps1') -Raw)
Set-Content -LiteralPath $script -Value $body -Encoding utf8
& (Join-Path $BrokerRoot 'tools\pr03b\vmware\submit.ps1') -Id $tag -Script $script -TimeoutSeconds 1800 -TailLines 85
exit $LASTEXITCODE
