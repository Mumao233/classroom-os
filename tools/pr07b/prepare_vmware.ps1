#requires -Version 7.0
<# Prepare one immutable source snapshot for the existing VMware broker. #>
[CmdletBinding()]
param(
    [ValidateSet('Targeted', 'Full')][string]$Mode = 'Targeted',
    [string[]]$TestTarget = @(),
    [string]$ValidationWheelhouse = (Join-Path $PSScriptRoot '..\..\work\pr07b\validation-wheels'),
    [string]$FullRunner = (Join-Path $PSScriptRoot 'run_combined.py'),
    [string]$BrokerRoot = 'J:\class-assistant\worktrees\web-pr03b-sessions'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$broker = Join-Path $BrokerRoot 'work\pr03b\vmware-broker'
$status = Get-Content -LiteralPath (Join-Path $broker 'broker.status.json') -Raw | ConvertFrom-Json
if ($status.PSObject.Properties['StoppedUtc'] -or -not (Get-Process -Id $status.Pid -ErrorAction SilentlyContinue)) {
    throw 'The existing VMware broker is unavailable.'
}
if (-not $status.Share.Ready -or $status.Share.Mode -ne 'readonly') {
    throw 'The existing read-only VMware share is unavailable.'
}
if ($Mode -eq 'Full' -and $TestTarget.Count) {
    throw 'Full verification requires the complete backend suite.'
}
if ($Mode -eq 'Full' -and -not (Test-Path -LiteralPath $FullRunner -PathType Leaf)) {
    throw 'The full VMware verification runner is unavailable.'
}
$wheels = @(Get-ChildItem -LiteralPath $ValidationWheelhouse -Filter '*.whl' -File)
if (-not $wheels.Count) {
    throw 'Prepare the pinned Windows Python 3.12 validation wheels first.'
}

$tag = 'pr07b-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$work = Join-Path $root "work\pr07b\vmware\$tag"
$transfer = Join-Path $broker $tag
$wheelTransfer = Join-Path $transfer 'validation-wheels'
$null = New-Item -ItemType Directory -Path $work, $transfer, $wheelTransfer
$wheelHashes = [ordered]@{}
foreach ($wheel in $wheels) {
    $wheelHashes[$wheel.Name] = (Get-FileHash -LiteralPath $wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    Copy-Item -LiteralPath $wheel.FullName -Destination $wheelTransfer
}

$runnerHash = $null
if ($Mode -eq 'Full') {
    Copy-Item -LiteralPath $FullRunner -Destination (Join-Path $transfer 'run_combined.py')
    $runnerHash = (Get-FileHash -LiteralPath (Join-Path $transfer 'run_combined.py') -Algorithm SHA256).Hash.ToLowerInvariant()
}

Add-Type -AssemblyName System.IO.Compression
$zipPath = Join-Path $work 'source.zip'
$files = @(& git -c core.quotepath=false -C $root ls-files --cached --others --exclude-standard)
if ($LASTEXITCODE -ne 0) { throw 'Source enumeration failed.' }
$hashes = [ordered]@{}
$stream = [IO.File]::Open($zipPath, [IO.FileMode]::CreateNew)
$zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($relative in ($files | Sort-Object -Unique)) {
        $source = Join-Path $root $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing source $relative" }
        $content = [IO.File]::ReadAllBytes($source)
        $hashes[$relative] = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($content)).ToLowerInvariant()
        $entry = $zip.CreateEntry($relative.Replace('\', '/'), [IO.Compression.CompressionLevel]::Optimal)
        $output = $entry.Open()
        try { $output.Write($content, 0, $content.Length) } finally { $output.Dispose() }
    }
} finally {
    $zip.Dispose()
    $stream.Dispose()
}
$sourceHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    base_sha = (& git -C $root rev-parse HEAD)
    branch = (& git -C $root branch --show-current)
    mode = $Mode
    source_zip_sha256 = $sourceHash
    files_sha256 = $hashes
    validation_wheels_sha256 = $wheelHashes
    runner_sha256 = $runnerHash
    test_targets = @($TestTarget)
}
$manifestPath = Join-Path $work 'SOURCE.json'
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM
Copy-Item -LiteralPath $zipPath, $manifestPath -Destination $transfer

$template = @'
$ErrorActionPreference = 'Stop'
$machine = Get-CimInstance Win32_ComputerSystem
if ($machine.Manufacturer -notmatch 'VMware') { throw 'VMware guest required.' }
if (-not (Test-Path 'J:\')) { & subst.exe J: C:\j }
$root = 'J:\class-assistant\worktrees\__TAG__'
if (Test-Path -LiteralPath $root) { throw 'Refusing to overwrite an existing guest snapshot.' }
$share = '\\vmware-host\Shared Folders\__SHARE__\work\pr03b\vmware-broker\__TAG__'
$python = 'C:\j\class-assistant\worktrees\web-pr04a-storage\work\pr04a\venv\Scripts\python.exe'
$pg = 'C:\j\class-assistant\worktrees\web-pr03b-sessions\work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec\runtime\Library\bin'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath "$pg\postgres.exe")) {
    throw 'Prepared guest toolchain unavailable.'
}
$null = New-Item -ItemType Directory -Path $root
$zipPath = Join-Path $root 'source.zip'
Copy-Item -LiteralPath "$share\source.zip" -Destination $zipPath
if ((Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne '__SHA__') {
    throw 'Source archive hash mismatch.'
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $root)
Copy-Item -LiteralPath "$share\SOURCE.json" -Destination "$root\SOURCE.json"
$null = New-Item -ItemType Directory -Path "$root\work\pr07b\tmp", "$root\outputs\pr07b" -Force
$env:TEMP = "$root\work\pr07b\tmp"
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PIP_CACHE_DIR = "$root\work\pr07b\pip-cache"
Set-Location -LiteralPath $root
$code = 1
$phases = [ordered]@{}
try {
    & $python -I -B -c "from pathlib import Path;import json,hashlib,platform;r=Path.cwd();m=json.loads((r/'SOURCE.json').read_text(encoding='utf-8'));bad=[p for p,h in m['files_sha256'].items() if hashlib.sha256((r/p).read_bytes()).hexdigest()!=h];print('guest',platform.node(),'python',platform.python_version(),'base',m['base_sha'],'files',len(m['files_sha256']),'mismatches',bad);assert not bad"
    $phases.source = $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) { throw 'Source manifest verification failed.' }
    & $python -I -B -m pip check > "$root\outputs\pr07b\dependencies.log"
    $phases.dependencies = $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) { throw 'Prepared Python dependency verification failed.' }
    & $python -I -B -m pip freeze > "$root\outputs\pr07b\dependency-versions.log"
    if ($LASTEXITCODE -ne 0) { throw 'Dependency version recording failed.' }
    $manifest = Get-Content -LiteralPath "$root\SOURCE.json" -Raw | ConvertFrom-Json
    foreach ($wheel in $manifest.validation_wheels_sha256.PSObject.Properties) {
        if ((Get-FileHash -LiteralPath "$share\validation-wheels\$($wheel.Name)" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $wheel.Value) {
            throw 'Validation wheel hash mismatch.'
        }
    }
    $ErrorActionPreference = 'Continue'
    & $python -I -X utf8 -B -m pip install --disable-pip-version-check --no-index --find-links "$share\validation-wheels" --target "$root\work\pr07b\validation-deps" -r automation/validation-requirements.txt > "$root\outputs\pr07b\validation-install.log" 2>&1
    $phases.install = $LASTEXITCODE
    & $python -I -X utf8 -B tools/overnight/check_contracts.py --deps "$root\work\pr07b\validation-deps" --output "$root\outputs\pr07b\contracts.json" > "$root\outputs\pr07b\contracts.log" 2>&1
    $phases.contracts = $LASTEXITCODE
    & $python -I -X utf8 -B tools/pr07b/check_contract_candidate.py --deps "$root\work\pr07b\validation-deps" --output "$root\outputs\pr07b\candidate-contract.json" > "$root\outputs\pr07b\candidate-contract.log" 2>&1
    $phases.candidate_contract = $LASTEXITCODE
    if ($manifest.mode -eq 'Full') {
        $ErrorActionPreference = 'Stop'
        Copy-Item -LiteralPath "$share\run_combined.py" -Destination "$root\work\pr07b\run_combined.py"
        if ((Get-FileHash -LiteralPath "$root\work\pr07b\run_combined.py" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.runner_sha256) {
            throw 'Full runner hash mismatch.'
        }
        Copy-Item -LiteralPath "$root\work\pr07b\run_combined.py" -Destination "$root\outputs\pr07b\run_combined.py"
        $null = New-Item -ItemType Junction -Path "$root\web\frontend\node_modules" -Target 'C:\j\class-assistant\worktrees\web-pr03b-sessions\web\frontend\node_modules'
        $ErrorActionPreference = 'Continue'
        & $python -I -X utf8 -B "$root\work\pr07b\run_combined.py" --root $root --auth-browser --deps "$root\work\pr07b\validation-deps" > "$root\outputs\pr07b\runner.log" 2>&1
        $phases.tests = $LASTEXITCODE
    } else {
        $targets = @()
        foreach ($target in $manifest.test_targets) { $targets += '--test-target'; $targets += [string]$target }
        & $python -I -X utf8 -B tools/pr11a/run_tests.py --pg-bin $pg --suite backend @targets > "$root\outputs\pr07b\runner.log" 2>&1
        $phases.tests = $LASTEXITCODE
    }
    $code = 0
    foreach ($value in $phases.Values) { if ($value -ne 0) { $code = 1 } }
} catch {
    $_.Exception.GetType().FullName | Set-Content -LiteralPath "$root\outputs\pr07b\preparation-error.log"
    $code = 1
} finally {
    $ErrorActionPreference = 'Continue'
    [ordered]@{exit_code=$code;phases=$phases;machine=$env:COMPUTERNAME;manufacturer=$machine.Manufacturer} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$root\outputs\pr07b\guest-result.json" -Encoding UTF8
    if (Test-Path -LiteralPath "$root\outputs\pr07b\runner.log") {
        Get-Content -LiteralPath "$root\outputs\pr07b\runner.log" -Tail 100
    }
    & $python -I -B -c "from pathlib import Path;import zipfile,hashlib;r=Path.cwd();p=r/'outputs/pr07b/evidence.zip';z=zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED);z.write(r/'SOURCE.json','SOURCE.json');[z.write(f,f.relative_to(r).as_posix()) for d in ['outputs/pr07b','outputs/pr11a','outputs/pr03a/third-round'] for f in (r/d).rglob('*') if f.is_file() and f.suffix in ['.json','.log','.xml','.py']];z.close();print('evidence',p,hashlib.sha256(p.read_bytes()).hexdigest())"
    if ($LASTEXITCODE -ne 0) { $code = 1 }
}
exit $code
'@

$script = Join-Path $broker "scripts\$tag.ps1"
$template.Replace('__TAG__', $tag).Replace('__SHA__', $sourceHash).Replace('__SHARE__', $status.Share.Name) |
    Set-Content -LiteralPath $script -Encoding utf8BOM
$job = [ordered]@{
    id = $tag
    mode = $Mode
    script = $script
    guest_root = "C:\j\class-assistant\worktrees\$tag"
    work = $work
    transfer = $transfer
    source_sha256 = $sourceHash
    runner_sha256 = $runnerHash
    submit = (Join-Path $BrokerRoot 'tools\pr03b\vmware\submit.ps1')
    evidence_guest = "C:\j\class-assistant\worktrees\$tag\outputs\pr07b\evidence.zip"
}
$job | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $work 'job.json') -Encoding utf8NoBOM
$job | ConvertTo-Json
