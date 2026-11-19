# SnapshotTag, TestMode and SourceHash are supplied by the host submitter.
$ErrorActionPreference = 'Stop'
$machine = Get-CimInstance Win32_ComputerSystem
if ($machine.Manufacturer -notmatch 'VMware' -and $machine.Model -notmatch 'VMware') { throw 'VMware required' }
if (-not (Test-Path 'J:\')) { $null = & subst.exe J: C:\j }
$root = "J:\class-assistant\worktrees\pr08b-runs\$SnapshotTag"
$share = "\\vmware-host\Shared Folders\pr03b-src\work\pr03b\vmware-broker\$SnapshotTag"
if (Test-Path -LiteralPath $root) { throw 'Run already exists' }
New-Item -ItemType Directory -Path "$root/work/pr08b", "$root/outputs/pr08b" -Force | Out-Null
Copy-Item -LiteralPath "$share/source.zip" -Destination "$root/work/pr08b/source.zip"
if ((Get-FileHash -LiteralPath "$root/work/pr08b/source.zip").Hash.ToLowerInvariant() -ne $SourceHash) { throw 'Archive hash mismatch' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory("$root/work/pr08b/source.zip", $root)
$manifest = Get-Content -LiteralPath "$share/SOURCE.json" -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($file in $manifest.files_sha256.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $root $file.Name)).Hash.ToLowerInvariant() -ne $file.Value) { throw "Source mismatch $($file.Name)" }
}
Copy-Item -LiteralPath "$share/SOURCE.json" -Destination "$root/outputs/pr08b/SOURCE.json"
$old = 'J:\class-assistant\worktrees\web-pr03b-sessions'
$python = 'J:\class-assistant\worktrees\web-pr04a-storage\work\pr04a\venv\Scripts\python.exe'
New-Item -ItemType Junction -Path "$root/web/frontend/node_modules" -Target "$old/web/frontend/node_modules" | Out-Null
New-Item -ItemType Junction -Path "$root/work/pr03b" -Target "$old/work/pr03b" | Out-Null
$env:PR08B_VMWARE_GUEST = '1'
$env:PR08B_DEPENDENCY_ROOT = $old
$env:PR04B_WEED = 'J:\class-assistant\worktrees\web-pr04a-storage\work\pr04a\seaweedfs\weed.exe'
$env:PR04B_PG_BIN = "$old/work/pr03b/toolchains/pg17.11-win64-53b5364a5a04e8ec/runtime/Library/bin"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = "$root/work/pr08b"
$env:TMP = $env:TEMP
$env:PATH = "$old/work/pr03b/vmware-broker/payload/node;$env:PATH"
Set-Location -LiteralPath $root
foreach ($wheel in $manifest.validation_wheels_sha256.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath "$share/validation-wheels/$($wheel.Name)").Hash.ToLowerInvariant() -ne $wheel.Value) { throw 'Validation wheel hash mismatch' }
}
$env:PIP_CACHE_DIR = "$root/work/pr08b/pip-cache"
if ($TestMode -eq 'legacy') {
    $python = 'J:\class-assistant\worktrees\pr06a-legacy-toolchain-20261117\venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Verified legacy Python toolchain is unavailable' }
    $env:PR08B_VALIDATION_DEPS = ''
} else {
    & $python -I -B -m pip install --disable-pip-version-check --no-index --find-links "$share/validation-wheels" --target "$root/work/pr08b/validation-deps" -r automation/validation-requirements.txt > "$root/outputs/pr08b/validation-deps.log"
    if ($LASTEXITCODE -ne 0) { Get-Content -LiteralPath "$root/outputs/pr08b/validation-deps.log" -Tail 20; exit $LASTEXITCODE }
    $env:PR08B_VALIDATION_DEPS = "$root/work/pr08b/validation-deps"
}
& $python -I -B tools/pr08b/run_guest.py $TestMode
$code = $LASTEXITCODE
$physicalRoot = "C:\j\class-assistant\worktrees\pr08b-runs\$SnapshotTag"
$remaining = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^(python|node|chrome|postgres|pg_ctl|weed)\.exe$' -and ($_.CommandLine -like "*$root*" -or $_.CommandLine -like "*$physicalRoot*")
} | Select-Object Name, ProcessId)
@{ remaining_owned_processes = $remaining; count = $remaining.Count } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath "$root/outputs/pr08b/cleanup.json" -Encoding UTF8
if ($remaining.Count) { $code = 1 }
$evidence = "$root/evidence"
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
Get-ChildItem -LiteralPath "$root/outputs/pr08b" -File | Where-Object Extension -In '.json', '.log', '.xml' | Copy-Item -Destination $evidence
if (Test-Path -LiteralPath "$root/outputs/pr08b/cache") {
    New-Item -ItemType Directory -Path "$evidence/cache" -Force | Out-Null
    Get-ChildItem -LiteralPath "$root/outputs/pr08b/cache" -File | Where-Object Extension -In '.json', '.log', '.xml' | Copy-Item -Destination "$evidence/cache"
}
Compress-Archive -Path "$evidence/*" -DestinationPath "$root/evidence.zip"
Write-Output "evidence=$root/evidence.zip sha256=$((Get-FileHash -LiteralPath "$root/evidence.zip").Hash.ToLowerInvariant()) exit=$code"
exit $code
