# Variables SnapshotTag and SourceHash are prepended by submit.ps1.
$ErrorActionPreference = 'Stop'
$machine = Get-CimInstance Win32_ComputerSystem
if ($machine.Manufacturer -notmatch 'VMware' -and $machine.Model -notmatch 'VMware') { throw 'VMware required' }
if (-not (Test-Path 'J:\')) { $null = & subst.exe J: C:\j }
$root = "J:\class-assistant\worktrees\pr08a-runs\$SnapshotTag"
$share = "\\vmware-host\Shared Folders\pr03b-src\work\pr03b\vmware-broker\$SnapshotTag"
if (Test-Path -LiteralPath $root) { throw 'Run exists' }
New-Item -ItemType Directory -Path "$root\work\pr08a" -Force | Out-Null
Copy-Item -LiteralPath "$share\source.zip" -Destination "$root\work\pr08a\source.zip"
if ((Get-FileHash "$root\work\pr08a\source.zip").Hash.ToLowerInvariant() -ne $SourceHash) { throw 'Source hash mismatch' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory("$root\work\pr08a\source.zip", $root)
$manifest = Get-Content -LiteralPath "$share\SOURCE.json" -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Combined -or $manifest.PSObject.Properties.Name -contains 'combined_capture') { $env:PR08A_COMBINED = '1' }
foreach ($file in $manifest.files_sha256.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $root $file.Name)).Hash.ToLowerInvariant() -ne $file.Value) { throw "Source mismatch $($file.Name)" }
}
$old = 'J:\class-assistant\worktrees\web-pr03b-sessions'
$python = 'J:\class-assistant\worktrees\web-pr04a-storage\work\pr04a\venv\Scripts\python.exe'
$pg = "$old\work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec\runtime\Library\bin"
New-Item -ItemType Junction -Path "$root\web\frontend\node_modules" -Target "$old\web\frontend\node_modules" | Out-Null
New-Item -ItemType Junction -Path "$root\work\pr03b" -Target "$old\work\pr03b" | Out-Null
$env:PR08A_VMWARE_GUEST = '1'
if ($BrowserOnly) { $env:PR08A_BROWSER_ONLY = '1' }
$env:PR05A_VMWARE_GUEST = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = "$root\work\pr08a"
$env:TMP = $env:TEMP
$env:PATH = "$old\work\pr03b\vmware-broker\payload\node;$env:PATH"
Set-Location -LiteralPath $root
foreach ($wheel in $manifest.validation_wheels_sha256.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath "$share\validation-wheels\$($wheel.Name)").Hash.ToLowerInvariant() -ne $wheel.Value) { throw 'Validation wheel hash mismatch' }
}
$env:PIP_CACHE_DIR = "$root\work\pr08a\pip-cache"
& $python -B -m pip install --disable-pip-version-check --no-index --find-links "$share\validation-wheels" --target "$root\work\pr08a\validation-deps" -r automation/validation-requirements.txt > "$root\work\pr08a\validation-deps.log"
if ($LASTEXITCODE -ne 0) { Get-Content "$root\work\pr08a\validation-deps.log" -Tail 20; exit $LASTEXITCODE }
$env:PR08A_VALIDATION_DEPS = "$root\work\pr08a\validation-deps"
& $python -B tools/pr08a/run_tests.py --pg-bin $pg --output "$root\outputs\pr08a" --dependency $old
$code = $LASTEXITCODE
Copy-Item -LiteralPath "$share\SOURCE.json" -Destination "$root\outputs\pr08a\SOURCE.json"
$physicalRoot = "C:\j\class-assistant\worktrees\pr08a-runs\$SnapshotTag"
$remaining = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^(python|node|chrome|postgres|pg_ctl)\.exe$' -and ($_.CommandLine -like "*$root*" -or $_.CommandLine -like "*$physicalRoot*")
} | Select-Object Name,ProcessId)
@{ remaining_owned_processes = $remaining; count = $remaining.Count } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath "$root\outputs\pr08a\cleanup.json" -Encoding UTF8
if ($remaining.Count) { $code = 1 }
$evidence = "$root\evidence"
New-Item -ItemType Directory -Path $evidence | Out-Null
Get-ChildItem -LiteralPath "$root\outputs\pr08a" -File | Where-Object Extension -In '.json', '.log', '.xml' | Copy-Item -Destination $evidence
Compress-Archive -Path "$evidence\*" -DestinationPath "$root\evidence.zip"
"evidence=$root\evidence.zip sha256=$((Get-FileHash "$root\evidence.zip").Hash.ToLowerInvariant()) exit=$code"
exit $code
