#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$BrokerRoot = 'J:\class-assistant\worktrees\web-pr03b-sessions',
    [Parameter(Mandatory)][ValidatePattern('^pr05a-[a-zA-Z0-9-]+$')][string]$RunId
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$staging = Join-Path $BrokerRoot 'work\pr03b\vmware-broker\scripts'
$statusPath = Join-Path $BrokerRoot 'work\pr03b\vmware-broker\broker.status.json'
$status = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
if ($status.PSObject.Properties['StoppedUtc'] -or -not (Get-Process -Id $status.Pid -ErrorAction SilentlyContinue)) { throw 'Broker is not running' }
if (-not $status.Share.Ready -or $status.Share.HostPath -ne $BrokerRoot) { throw 'Expected read-only broker share is unavailable' }
$archive = Join-Path $staging "$RunId.zip"
$bootstrap = Join-Path $staging "$RunId.ps1"
$guestRunner = Join-Path $staging "$RunId-guest.ps1"
foreach ($path in @($archive, $bootstrap, $guestRunner)) {
    if (Test-Path -LiteralPath $path) { throw "Staging destination already exists $path" }
}
$files = Get-ChildItem -LiteralPath $PSScriptRoot -File | Sort-Object Name
Compress-Archive -LiteralPath $files.FullName -DestinationPath $archive
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'guest-run.ps1') -Destination $guestRunner
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
$share = "\\vmware-host\Shared Folders\$($status.Share.Name)\work\pr03b\vmware-broker\scripts"
$guestText = Get-Content -LiteralPath $guestRunner -Raw -Encoding utf8
"& {`n$guestText`n} -Archive '$share\$RunId.zip' -RunId '$RunId' -ArchiveSha256 '$hash'`nexit `$LASTEXITCODE" |
    Set-Content -LiteralPath $bootstrap -Encoding utf8
$receipt = Join-Path $repo "outputs\pr05a\$RunId"
New-Item -ItemType Directory -Path $receipt | Out-Null
[ordered]@{
    run_id = $RunId
    base_sha = (& git -C $repo rev-parse HEAD)
    branch = (& git -C $repo branch --show-current)
    archive_sha256 = $hash
    staged_archive = $archive
    bootstrap = $bootstrap
    vmx = $status.VmxPath
    source_sha256 = @($files | ForEach-Object {
        @{ file = $_.Name; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    })
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $receipt 'source-receipt.json') -Encoding utf8
"Prepared $RunId archive_sha256=$hash"
"Submit with $BrokerRoot\tools\pr03b\vmware\submit.ps1 -Id $RunId -Script $bootstrap -TimeoutSeconds 600"
