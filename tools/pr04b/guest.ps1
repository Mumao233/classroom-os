# Parameters are prepended by the host submitter. VMware runs this in Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'
if (-not (Test-Path 'J:\')) { $null = & subst.exe J: C:\j }
$root = "C:\j\class-assistant\worktrees\pr04b-runs\$SnapshotTag"
$share = "\\vmware-host\Shared Folders\pr03b-src\work\pr03b\vmware-broker\$SnapshotTag"
if (Test-Path $root) { throw 'Snapshot already exists' }
$null = New-Item -ItemType Directory -Path "$root\work\pr04b", "$root\outputs\pr04b"
Copy-Item "$share\source.zip" "$root\work\pr04b\source.zip"
if ((Get-FileHash "$root\work\pr04b\source.zip").Hash.ToLowerInvariant() -ne $SourceHash) { throw 'Archive hash mismatch' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory("$root\work\pr04b\source.zip", $root)
Copy-Item "$share\SOURCE.json" "$root\outputs\pr04b\SOURCE.json"
$manifest = Get-Content "$share\SOURCE.json" -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($p in $manifest.files_sha256.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $root $p.Name)).Hash.ToLowerInvariant() -ne $p.Value) { throw "Source mismatch $($p.Name)" }
}
"verified base=$($manifest.base_sha) files=$(@($manifest.files_sha256.PSObject.Properties).Count) zip=$SourceHash"
# Read-only reuse of the toolchain already prepared in the guest for PR-03b/PR-04a.
$old = 'C:\j\class-assistant\worktrees\web-pr03b-sessions'
$python = 'C:\j\class-assistant\worktrees\web-pr04a-storage\work\pr04a\venv\Scripts\python.exe'
$env:PR04B_WEED = 'C:\j\class-assistant\worktrees\web-pr04a-storage\work\pr04a\seaweedfs\weed.exe'
$env:PR04B_PG_BIN = "$old\work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec\runtime\Library\bin"
$env:PR05A_DEPENDENCY_ROOT = $old
$env:PR05A_REVIEW_OUTPUT = "$root\outputs\pr04b"
$env:PR05A_VMWARE_GUEST = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = "$old\work\pr03b\vmware-broker\payload\ms-playwright"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = "$root\work\pr04b"; $env:TMP = $env:TEMP
Set-Location $root
& $python -I -B -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$wheels = @($manifest.validation_wheels_sha256.PSObject.Properties)
if ($wheels.Count) {
    foreach ($wheel in $wheels) {
        if ((Get-FileHash -LiteralPath "$share\validation-wheels\$($wheel.Name)").Hash.ToLowerInvariant() -ne $wheel.Value) { throw 'Validation wheel hash mismatch' }
    }
    $env:PIP_CACHE_DIR = "$root\work\pr04b\pip-cache"
    & $python -I -X utf8 -B -m pip install --disable-pip-version-check --no-index --find-links "$share\validation-wheels" --target "$root\work\pr04b\validation-deps" -r automation/validation-requirements.txt > "$root\outputs\pr04b\validation-deps.log"
    if ($LASTEXITCODE -ne 0) { Get-Content "$root\outputs\pr04b\validation-deps.log" -Tail 20; exit $LASTEXITCODE }
    $env:PR04B_VALIDATION_DEPS = "$root\work\pr04b\validation-deps"
}
& $python -I -B "$root\tools\pr04b\run_guest.py" $TestMode
$code = $LASTEXITCODE
Compress-Archive -Path "$root\outputs\pr04b" -DestinationPath "$root\evidence.zip"
"evidence=$root\evidence.zip sha256=$((Get-FileHash "$root\evidence.zip").Hash.ToLowerInvariant()) exit=$code"
exit $code
