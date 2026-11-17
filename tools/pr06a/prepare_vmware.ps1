#requires -Version 7.0
<# Freeze source and prepare an offline validation job for the existing VMware broker. #>
[CmdletBinding()]
param(
    [string]$SourceRef,
    [switch]$Full,
    [string]$LegacyPython,
    [string]$ValidationWheelhouse = 'J:\class-assistant\worktrees\web-pr07b-summary-versions\work\pr07b\validation-wheels',
    [string]$BrokerRoot = 'J:\class-assistant\worktrees\web-pr03b-sessions'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$broker = Join-Path $BrokerRoot 'work\pr03b\vmware-broker'
$status = Get-Content -LiteralPath (Join-Path $broker 'broker.status.json') -Raw | ConvertFrom-Json
if ($status.PSObject.Properties['StoppedUtc'] -or -not (Get-Process -Id $status.Pid -ErrorAction SilentlyContinue)) {
    throw 'Existing VMware broker is unavailable.'
}
if (-not $status.Share.Ready -or $status.Share.Mode -ne 'readonly') { throw 'Read-only share is unavailable.' }
if ($Full -and -not $LegacyPython) { throw 'Full validation needs the prepared guest Python 3.10 path.' }
$tag = 'pr06a-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$guestId = [Guid]::NewGuid().ToString('N').Substring(0, 8)
$guestRoot = "J:\p06a\$guestId"
$work = Join-Path $root "work\pr06a\vmware\$tag"
$transfer = Join-Path $broker $tag
$null = New-Item -ItemType Directory -Path $work, $transfer, "$transfer\validation-wheels"
$source = $root
$base = & git -C $root rev-parse HEAD
if ($SourceRef) {
    $base = & git -C $root rev-parse $SourceRef
    & git -C $root archive --format=zip --output="$work\base.zip" $base
    if ($LASTEXITCODE -ne 0) { throw 'Baseline archive failed.' }
    $source = Join-Path $work 'base'
    Expand-Archive -LiteralPath "$work\base.zip" -DestinationPath $source
    $files = @(Get-ChildItem -LiteralPath $source -File -Recurse | ForEach-Object { [IO.Path]::GetRelativePath($source, $_.FullName).Replace('\', '/') })
} else {
    $files = @(& git -c core.quotepath=false -C $root ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) { throw 'Source enumeration failed.' }
}
Add-Type -AssemblyName System.IO.Compression
$hashes = [ordered]@{}
$stream = [IO.File]::Open("$work\source.zip", [IO.FileMode]::CreateNew)
$archive = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($relative in ($files | Sort-Object -Unique)) {
        $bytes = [IO.File]::ReadAllBytes((Join-Path $source $relative))
        $hashes[$relative] = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
        $entry = $archive.CreateEntry($relative, [IO.Compression.CompressionLevel]::Optimal).Open()
        try { $entry.Write($bytes, 0, $bytes.Length) } finally { $entry.Dispose() }
    }
} finally { $archive.Dispose(); $stream.Dispose() }
$sourceHash = (Get-FileHash -LiteralPath "$work\source.zip" -Algorithm SHA256).Hash.ToLowerInvariant()
& git -C $root bundle create "$transfer\history.bundle" HEAD
if ($LASTEXITCODE -ne 0) { throw 'Offline history export failed.' }
$dependencyHashes = [ordered]@{}
foreach ($wheel in (Get-ChildItem -LiteralPath $ValidationWheelhouse -Filter '*.whl' -File)) {
    $dependencyHashes[$wheel.Name] = (Get-FileHash -LiteralPath $wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    Copy-Item -LiteralPath $wheel.FullName -Destination "$transfer\validation-wheels"
}
if (-not $dependencyHashes.Count) { throw 'Pinned validation wheelhouse is missing.' }
$runner = Join-Path $PSScriptRoot 'run_vmware_validation.py'
Copy-Item -LiteralPath $runner -Destination "$transfer\run_vmware_validation.py"
$manifest = [ordered]@{
    base_sha = $base
    branch = (& git -C $root branch --show-current)
    source_zip_sha256 = $sourceHash
    files_sha256 = $hashes
    runner_sha256 = (Get-FileHash -LiteralPath $runner -Algorithm SHA256).Hash.ToLowerInvariant()
    bundle_sha256 = (Get-FileHash -LiteralPath "$transfer\history.bundle" -Algorithm SHA256).Hash.ToLowerInvariant()
    validation_wheels_sha256 = $dependencyHashes
    full = [bool]$Full
    legacy_python = $LegacyPython
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath "$work\SOURCE.json" -Encoding utf8NoBOM
Copy-Item -LiteralPath "$work\source.zip", "$work\SOURCE.json" -Destination $transfer
$template = @'
$ErrorActionPreference = 'Stop'
$machine = Get-CimInstance Win32_ComputerSystem
if ($machine.Manufacturer -notmatch 'VMware') { throw 'VMware guest required.' }
if (-not (Test-Path 'J:\')) { & subst.exe J: C:\j }
$root = '__GUEST_ROOT__'
if (Test-Path -LiteralPath $root) { throw 'Refusing to overwrite a guest snapshot.' }
$share = '\\vmware-host\Shared Folders\__SHARE__\work\pr03b\vmware-broker\__TAG__'
$git = 'C:\j\class-assistant\worktrees\pr03b-integration-20261114-01\work\pr03b\integration-20261114-01\git\cmd\git.exe'
$python = 'C:\j\class-assistant\worktrees\web-pr04a-storage\work\pr04a\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $git -PathType Leaf)) {
    throw 'Prepared guest Python or Git executable is missing.'
}
$manifest = Get-Content -LiteralPath "$share\SOURCE.json" -Raw | ConvertFrom-Json
foreach ($entry in @(@('source.zip', $manifest.source_zip_sha256), @('history.bundle', $manifest.bundle_sha256), @('run_vmware_validation.py', $manifest.runner_sha256))) {
    if ((Get-FileHash -LiteralPath "$share\$($entry[0])" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry[1]) { throw 'Transfer hash mismatch.' }
}
$env:GIT_CONFIG_NOSYSTEM = '1'
$env:GIT_CONFIG_GLOBAL = 'NUL'
$env:PATH = (Split-Path $git) + ';' + $env:PATH
& $git clone --no-checkout "$share\history.bundle" $root
if ($LASTEXITCODE -ne 0) { throw 'Offline Git clone failed.' }
& $git -C $root reset --mixed $manifest.base_sha
if ($LASTEXITCODE -ne 0) { throw 'Snapshot Git index initialization failed.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory("$share\source.zip", $root)
Copy-Item -LiteralPath "$share\SOURCE.json" -Destination "$root\SOURCE.json"
$null = New-Item -ItemType Directory -Path "$root\work\pr06a\tmp", "$root\outputs\pr06a" -Force
$env:TEMP = "$root\work\pr06a\tmp"
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PIP_CACHE_DIR = "$root\work\pr06a\pip-cache"
Set-Location -LiteralPath $root
foreach ($wheel in $manifest.validation_wheels_sha256.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath "$share\validation-wheels\$($wheel.Name)" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $wheel.Value) { throw 'Wheel hash mismatch.' }
}
$ErrorActionPreference = 'Continue'
$global:LASTEXITCODE = $null
& $python -I -X utf8 -B -m pip install --disable-pip-version-check --no-index --find-links "$share\validation-wheels" --target "$root\work\pr06a\validation-deps" -r automation/validation-requirements.txt > "$root\outputs\pr06a\validation-install.log" 2>&1
if ($null -eq $LASTEXITCODE -or $LASTEXITCODE -ne 0) {
    'Offline validation dependency preparation failed.' | Set-Content -LiteralPath "$root\outputs\pr06a\preparation-error.log"
    exit 1
}
Copy-Item -LiteralPath "$share\run_vmware_validation.py" -Destination "$root\work\pr06a\run_vmware_validation.py"
if ((Get-FileHash -LiteralPath "$root\work\pr06a\run_vmware_validation.py" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.runner_sha256) {
    throw 'Executed validation runner hash mismatch.'
}
$arguments = @('--root', $root, '--deps', "$root\work\pr06a\validation-deps")
if ($manifest.full) {
    $null = New-Item -ItemType Junction -Path "$root\web\frontend\node_modules" -Target 'C:\j\class-assistant\worktrees\web-pr03b-sessions\web\frontend\node_modules'
    $arguments += '--full'
}
if ($manifest.legacy_python) {
    $arguments += '--legacy-python'
    $arguments += $manifest.legacy_python
}
$global:LASTEXITCODE = $null
& $python -I -X utf8 -B "$root\work\pr06a\run_vmware_validation.py" @arguments > "$root\outputs\pr06a\runner.log" 2>&1
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 1 }
if (-not (Test-Path -LiteralPath "$root\outputs\pr06a\validation.json" -PathType Leaf) -or
    -not (Test-Path -LiteralPath "$root\outputs\pr06a\evidence.zip" -PathType Leaf)) {
    'Validation did not produce complete result evidence.' | Set-Content -LiteralPath "$root\outputs\pr06a\preparation-error.log"
    $code = 1
} else {
    try {
        $validation = Get-Content -LiteralPath "$root\outputs\pr06a\validation.json" -Raw | ConvertFrom-Json
        if ($validation.passed -isnot [bool] -or $validation.passed -ne ($code -eq 0)) {
            throw 'Validation exit code and result disagree.'
        }
    } catch {
        'Validation result is invalid or inconsistent.' | Set-Content -LiteralPath "$root\outputs\pr06a\preparation-error.log"
        $code = 1
    }
}
Get-Content -LiteralPath "$root\outputs\pr06a\runner.log" -Tail 15
"guest_exit=$code evidence=$root\outputs\pr06a\evidence.zip"
exit $code
'@
$script = Join-Path $broker "scripts\$tag.ps1"
$template.Replace('__TAG__', $tag).Replace('__SHARE__', $status.Share.Name).Replace('__GUEST_ROOT__', $guestRoot) | Set-Content -LiteralPath $script -Encoding utf8BOM
$job = [ordered]@{ id=$tag; script=$script; work=$work; source_sha256=$sourceHash;
    guest_root=$guestRoot;
    submit=(Join-Path $BrokerRoot 'tools\pr03b\vmware\submit.ps1');
    evidence_guest="C:\j\p06a\$guestId\outputs\pr06a\evidence.zip" }
$job | ConvertTo-Json | Set-Content -LiteralPath "$work\job.json" -Encoding utf8NoBOM
$job | ConvertTo-Json
