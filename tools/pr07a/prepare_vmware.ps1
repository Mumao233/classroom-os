#requires -Version 7.0
[CmdletBinding()]
param(
    [string[]]$TestTarget=@(),
    [string]$ValidationWheelhouse=(Join-Path $PSScriptRoot '..\..\work\pr07a\validation-wheels')
)
$ErrorActionPreference='Stop'
$root=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$broker='J:\class-assistant\worktrees\web-pr03b-sessions\work\pr03b\vmware-broker'
$tag='pr07a-'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
$work=Join-Path $root "work\pr07a\vmware\$tag"
$transfer=Join-Path $broker $tag
$null=New-Item -ItemType Directory -Path $work,$transfer
$wheels=@(Get-ChildItem -LiteralPath $ValidationWheelhouse -Filter '*.whl' -File)
if ($wheels.Count -eq 0) { throw 'Prepare the pinned Windows Python 3.12 validation wheels first' }
$wheelHashes=[ordered]@{}
$wheelTransfer=Join-Path $transfer 'validation-wheels'
$null=New-Item -ItemType Directory -Path $wheelTransfer
foreach ($wheel in $wheels) {
    $wheelHashes[$wheel.Name]=(Get-FileHash -LiteralPath $wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    Copy-Item -LiteralPath $wheel.FullName -Destination $wheelTransfer
}
Add-Type -AssemblyName System.IO.Compression
$zipPath=Join-Path $work 'source.zip'
$files=@(& git -c core.quotepath=false -C $root ls-files --cached --others --exclude-standard)
if ($LASTEXITCODE -ne 0) { throw 'Source enumeration failed' }
$hashes=[ordered]@{}
$stream=[IO.File]::Open($zipPath,[IO.FileMode]::CreateNew)
$zip=[IO.Compression.ZipArchive]::new($stream,[IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($relative in ($files | Sort-Object -Unique)) {
        $source=Join-Path $root $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing source $relative" }
        $hashes[$relative]=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        $entry=$zip.CreateEntry($relative.Replace('\','/'),[IO.Compression.CompressionLevel]::Optimal)
        $input=[IO.File]::OpenRead($source)
        $output=$entry.Open()
        try { $input.CopyTo($output) } finally { $output.Dispose();$input.Dispose() }
    }
} finally { $zip.Dispose();$stream.Dispose() }
$sha=(Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest=[ordered]@{
    base_sha=(& git -C $root rev-parse HEAD)
    branch=(& git -C $root branch --show-current)
    source_zip_sha256=$sha
    files_sha256=$hashes
    validation_wheels_sha256=$wheelHashes
    test_targets=@($TestTarget)
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $work 'SOURCE.json') -Encoding utf8NoBOM
Copy-Item -LiteralPath $zipPath,(Join-Path $work 'SOURCE.json') -Destination $transfer
$template=@'
$ErrorActionPreference='Stop'
if (-not (Test-Path 'J:\')) { & subst.exe J: C:\j }
$root='J:\class-assistant\worktrees\__TAG__'
if (Test-Path -LiteralPath $root) { throw 'Refusing to overwrite an existing guest snapshot' }
$share='\\vmware-host\Shared Folders\pr03b-src\work\pr03b\vmware-broker\__TAG__'
$python='C:\j\class-assistant\worktrees\web-pr04a-storage\work\pr04a\venv\Scripts\python.exe'
$pg='C:\j\class-assistant\worktrees\web-pr03b-sessions\work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec\runtime\Library\bin'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath "$pg\postgres.exe")) { throw 'Prepared guest toolchain unavailable' }
$null=New-Item -ItemType Directory -Path $root
$zipPath=Join-Path $root 'source.zip'
Copy-Item -LiteralPath "$share\source.zip" -Destination $zipPath
if ((Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne '__SHA__') { throw 'Source archive hash mismatch' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory($zipPath,$root)
Copy-Item -LiteralPath "$share\SOURCE.json" -Destination "$root\SOURCE.json"
$null=New-Item -ItemType Directory -Path "$root\work\pr07a\tmp","$root\outputs\pr07a" -Force
$env:TEMP="$root\work\pr07a\tmp"
$env:TMP=$env:TEMP
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PIP_CACHE_DIR="$root\work\pr07a\pip-cache"
Set-Location -LiteralPath $root
& $python -I -B -c "from pathlib import Path;import json,hashlib,platform;r=Path.cwd();m=json.loads((r/'SOURCE.json').read_text(encoding='utf-8'));bad=[p for p,h in m['files_sha256'].items() if hashlib.sha256((r/p).read_bytes()).hexdigest()!=h];print('guest',platform.node(),'python',platform.python_version(),'base',m['base_sha'],'files',len(m['files_sha256']),'mismatches',bad);assert not bad"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -I -B -m pip check > "$root\outputs\pr07a\dependencies.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -I -B -m pip freeze > "$root\outputs\pr07a\dependency-versions.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$manifest=Get-Content -LiteralPath "$root\SOURCE.json" -Raw | ConvertFrom-Json
foreach ($wheel in $manifest.validation_wheels_sha256.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath "$share\validation-wheels\$($wheel.Name)" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $wheel.Value) {
        throw 'Validation wheel hash mismatch'
    }
}
$targets=@()
foreach ($target in $manifest.test_targets) { $targets+='--test-target';$targets+=[string]$target }
$ErrorActionPreference='Continue'
& $python -I -X utf8 -B -m pip install --disable-pip-version-check --no-index --find-links "$share\validation-wheels" --target "$root\work\pr07a\validation-deps" -r automation/validation-requirements.txt > "$root\outputs\pr07a\validation-install.log" 2>&1
$installCode=$LASTEXITCODE
& $python -I -X utf8 -B tools/overnight/check_contracts.py --deps "$root\work\pr07a\validation-deps" --output "$root\outputs\pr07a\contracts.json" > "$root\outputs\pr07a\contracts.log" 2>&1
$contractCode=$LASTEXITCODE
& $python -I -X utf8 -B tools/pr11a/run_tests.py --pg-bin $pg --suite backend @targets > "$root\outputs\pr07a\runner.log" 2>&1
$code=$LASTEXITCODE
if ($contractCode -ne 0) { $code=$contractCode }
if ($installCode -ne 0) { $code=$installCode }
$ErrorActionPreference='Stop'
Get-Content -LiteralPath "$root\outputs\pr07a\runner.log" -Tail 100
& $python -I -B -c "from pathlib import Path;import zipfile,hashlib;r=Path.cwd();p=r/'outputs/pr07a/evidence.zip';z=zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED);z.write(r/'SOURCE.json','SOURCE.json');[z.write(f,f.relative_to(r).as_posix()) for d in ['outputs/pr07a','outputs/pr11a','outputs/pr03a/third-round'] for f in (r/d).rglob('*') if f.is_file() and f.suffix in ['.json','.log','.xml']];z.close();print('evidence',p,hashlib.sha256(p.read_bytes()).hexdigest())"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
exit $code
'@
$script=Join-Path $broker "scripts\$tag.ps1"
$template.Replace('__TAG__',$tag).Replace('__SHA__',$sha) | Set-Content -LiteralPath $script -Encoding utf8BOM
[ordered]@{id=$tag;script=$script;guest_root="C:\j\class-assistant\worktrees\$tag";work=$work;transfer=$transfer;source_sha256=$sha} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $work 'job.json') -Encoding utf8NoBOM
Get-Content -LiteralPath (Join-Path $work 'job.json')
