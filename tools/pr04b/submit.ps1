#requires -Version 7.0
<# Host side: snapshot this worktree and queue one run on the existing VMware broker.
   Holds no credentials. Tests run only inside the guest. #>
[CmdletBinding()]
param(
    [ValidateSet('probe','targeted','full')][string]$Mode = 'targeted',
    [string]$Python = 'C:\Users\Mumao\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe',
    [string]$BrokerRoot = 'J:\class-assistant\worktrees\web-pr03b-sessions',
    [int]$TimeoutSeconds = 1800,
    # Pinned validation wheels prepared for PR-07b; needed by the complete suite only.
    [string]$ValidationWheelhouse = 'J:\class-assistant\worktrees\web-pr07b-summary-versions\work\pr07b\validation-wheels'
)
$ErrorActionPreference = 'Stop'
$tag = 'pr04b-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$broker = Join-Path $BrokerRoot 'work\pr03b\vmware-broker'
$payload = Join-Path $broker $tag
if ($Mode -eq 'full') { & $Python -B (Join-Path $PSScriptRoot 'prepare_payload.py') $payload $ValidationWheelhouse }
else { & $Python -B (Join-Path $PSScriptRoot 'prepare_payload.py') $payload }
if ($LASTEXITCODE -ne 0) { throw 'source preparation failed' }
$hash = (Get-FileHash (Join-Path $payload 'source.zip')).Hash.ToLowerInvariant()
$script = Join-Path $broker "scripts\$tag.ps1"
$body = "`$SnapshotTag = '$tag'`r`n`$TestMode = '$Mode'`r`n`$SourceHash = '$hash'`r`n" + (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'guest.ps1') -Raw)
Set-Content -LiteralPath $script -Value $body -Encoding utf8
"tag=$tag mode=$Mode source_sha256=$hash"
& (Join-Path $BrokerRoot 'tools\pr03b\vmware\submit.ps1') -Id $tag -Script $script -TimeoutSeconds $TimeoutSeconds -TailLines 150
exit $LASTEXITCODE
