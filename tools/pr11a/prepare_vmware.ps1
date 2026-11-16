#requires -Version 7.0
[CmdletBinding()]
param([ValidateSet('billing','backend','integration')][string]$Suite='billing')
$ErrorActionPreference='Stop'
$root=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$broker='J:\class-assistant\worktrees\web-pr03b-sessions\work\pr03b\vmware-broker'
$tag='pr11a-'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
$work=Join-Path $root "work\pr11a\vmware\$tag"
$transfer=Join-Path $broker $tag
$null=New-Item -ItemType Directory -Path $work,$transfer
Add-Type -AssemblyName System.IO.Compression
$zipPath=Join-Path $work 'source.zip'
$files=@(& git -c core.quotepath=false -C $root ls-files --cached --others --exclude-standard)
if ($LASTEXITCODE -ne 0) { throw 'Source enumeration failed' }
if ($Suite -eq 'integration') {
    $inputRoot=Join-Path $root 'work\pr11a\integration-inputs'
    $null=New-Item -ItemType Directory -Path $inputRoot -Force
    foreach ($name in @('0009_private_media.py','0009_private_media.sql')) {
        $source=Join-Path 'J:\class-assistant\worktrees\web-pr04a-storage\web\backend\migrations\versions' $name
        Copy-Item -LiteralPath $source -Destination (Join-Path $inputRoot $name)
        $files+='work/pr11a/integration-inputs/'+$name
    }
}
$hashes=[ordered]@{}
$stream=[IO.File]::Open($zipPath,[IO.FileMode]::CreateNew)
$zip=[IO.Compression.ZipArchive]::new($stream,[IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($relative in ($files | Sort-Object -Unique)) {
        $source=Join-Path $root $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing source $relative" }
        $hashes[$relative]=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        $entry=$zip.CreateEntry($relative.Replace('\','/'),[IO.Compression.CompressionLevel]::Optimal)
        $input=[IO.File]::OpenRead($source);$output=$entry.Open()
        try { $input.CopyTo($output) } finally { $output.Dispose();$input.Dispose() }
    }
} finally { $zip.Dispose();$stream.Dispose() }
$sha=(Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest=[ordered]@{base_sha=(& git -C $root rev-parse HEAD);branch=(& git -C $root branch --show-current);source_zip_sha256=$sha;files_sha256=$hashes}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $work 'SOURCE.json') -Encoding utf8NoBOM
Copy-Item -LiteralPath $zipPath,(Join-Path $work 'SOURCE.json') -Destination $transfer
$template=@'
$ErrorActionPreference='Stop'
if (-not (Test-Path 'J:\')) { & subst.exe J: C:\j }
$root='J:\class-assistant\worktrees\__TAG__'
if (Test-Path -LiteralPath $root) { throw 'Refusing to overwrite an existing guest snapshot' }
$share='\\vmware-host\Shared Folders\pr03b-src\work\pr03b\vmware-broker\__TAG__'
$old='J:\class-assistant\worktrees\web-pr03b-sessions'
$python='J:\class-assistant\worktrees\pr03b-integration-20261114-01\work\pr03b\integration-20261114-01\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Frozen guest Python environment unavailable' }
$null=New-Item -ItemType Directory -Path $root
$zipPath=Join-Path $root 'source.zip'
Copy-Item -LiteralPath "$share\source.zip" -Destination $zipPath
if ((Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne '__SHA__') { throw 'Source archive hash mismatch' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory($zipPath,$root)
Copy-Item -LiteralPath "$share\SOURCE.json" -Destination "$root\SOURCE.json"
$null=New-Item -ItemType Directory -Path "$root\work\pr11a\tmp","$root\outputs\pr11a" -Force
$env:TEMP="$root\work\pr11a\tmp";$env:TMP=$env:TEMP
$env:PYTHONUTF8='1';$env:PYTHONDONTWRITEBYTECODE='1'
Set-Location -LiteralPath $root
& $python -I -B -c "from pathlib import Path;import json,hashlib,platform;r=Path.cwd();m=json.loads((r/'SOURCE.json').read_text(encoding='utf-8'));bad=[p for p,h in m['files_sha256'].items() if hashlib.sha256((r/p).read_bytes()).hexdigest()!=h];print('guest',platform.node(),'python',platform.python_version(),'base',m['base_sha'],'files',len(m['files_sha256']),'mismatches',bad);assert not bad"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -I -B -m pip check > "$root\outputs\pr11a\dependencies.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -I -B -m pip freeze > "$root\outputs\pr11a\dependency-versions.log"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -B tools/overnight/check_contracts.py --output "$root\outputs\pr11a\contracts.json" > "$root\outputs\pr11a\contracts.log" 2>&1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -I -B tools/pr11a/run_tests.py --pg-bin "$old\work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec\runtime\Library\bin" --suite __SUITE__ > "$root\outputs\pr11a\runner.log" 2>&1
$code=$LASTEXITCODE
Get-Content -LiteralPath "$root\outputs\pr11a\runner.log" -Tail 120
& $python -I -B -c "from pathlib import Path;import zipfile,hashlib;r=Path.cwd();p=r/'outputs/pr11a/evidence.zip';z=zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED);z.write(r/'SOURCE.json','SOURCE.json');[z.write(f,f.relative_to(r).as_posix()) for d in ['outputs/pr11a','outputs/pr03a/third-round'] for f in (r/d).rglob('*') if f.is_file() and f.suffix in ['.json','.log','.xml']];z.close();print('evidence',p,hashlib.sha256(p.read_bytes()).hexdigest())"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
exit $code
'@
$script=Join-Path $broker "scripts\$tag.ps1"
$template.Replace('__TAG__',$tag).Replace('__SHA__',$sha).Replace('__SUITE__',$Suite) | Set-Content -LiteralPath $script -Encoding utf8BOM
[ordered]@{id=$tag;script=$script;guest_root="J:\class-assistant\worktrees\$tag";work=$work;transfer=$transfer;suite=$Suite;source_sha256=$sha} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $work 'job.json') -Encoding utf8NoBOM
Get-Content -LiteralPath (Join-Path $work 'job.json')
