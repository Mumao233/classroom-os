# SnapshotTag, Suite and SourceHash are supplied by prepare_vmware.ps1.
$ErrorActionPreference = 'Stop'
$machine = Get-CimInstance Win32_ComputerSystem
if ($machine.Manufacturer -notmatch 'VMware' -and $machine.Model -notmatch 'VMware') { throw 'VMware TEST guest required' }
# The TEST guest has a single C: volume; its established workspace (shared with the PR-03b, 04b,
# 06c and 11a runners) is J: substituted for C:\j. Map it when absent, then verify the mapping
# before creating anything, so a different J: never receives data and the broker's C:\j
# evidence path always names the files written here.
if (-not (Test-Path -LiteralPath 'J:\')) { & subst.exe J: C:\j }
if (@(& subst.exe) -notcontains 'J:\: => C:\j') { throw 'Guest J: must be the C:\j workspace substitution' }
if ($SnapshotTag -notmatch '^pr11c-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$' -or $Suite -notin @('targeted', 'full')) {
    throw 'Invalid PR-11c job'
}
$root = "J:\class-assistant\worktrees\$SnapshotTag"
$share = "\\vmware-host\Shared Folders\pr03b-src\work\pr03b\vmware-broker\$SnapshotTag"
if (Test-Path -LiteralPath $root) { throw 'Guest snapshot already exists' }
$old = 'J:\class-assistant\worktrees\web-pr03b-sessions'
$storage = 'J:\class-assistant\worktrees\web-pr04a-storage'
$python = "$storage\work\pr04a\venv\Scripts\python.exe"
$weed = "$storage\work\pr04a\seaweedfs\weed.exe"
$node = "$old\work\pr03b\vmware-broker\payload\node\node.exe"
$pg = "$old\work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec\runtime\Library\bin"
$sourceModules = "$old\web\frontend\node_modules"
$work = "$root\work\pr11c"
$out = "$root\outputs\pr11c"
$null = New-Item -ItemType Directory -Path $work, $out -Force
$code = 1
try {
    $archive = "$work\source.zip"
    Copy-Item -LiteralPath "$share\source.zip" -Destination $archive
    if ((Get-FileHash -LiteralPath $archive).Hash.ToLowerInvariant() -ne $SourceHash) { throw 'Archive hash mismatch' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $root)
    Copy-Item -LiteralPath "$share\SOURCE.json" -Destination "$out\SOURCE.json"
    $manifest = Get-Content -LiteralPath "$out\SOURCE.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($file in $manifest.files_sha256.PSObject.Properties) {
        $hash = (Get-FileHash -LiteralPath (Join-Path $root $file.Name)).Hash.ToLowerInvariant()
        if ($hash -ne $file.Value) { throw "Source hash mismatch $($file.Name)" }
    }
    foreach ($dependency in @($python, $weed, $node, "$pg\postgres.exe", "$sourceModules\vitest\vitest.mjs")) {
        if (-not (Test-Path -LiteralPath $dependency -PathType Leaf)) { throw "Missing guest dependency $dependency" }
    }
    # PR-08c added Pillow; the shared PR-04a venv predates it, so install it offline into this snapshot only.
    $env:TEMP = $work
    $env:TMP = $work
    $env:PIP_CACHE_DIR = "$work\pip-cache"
    $wheels = Join-Path (Split-Path $share -Parent) 'pr08c-media-gate-r0-0b289f8\validation-wheels'
    & $python -I -B -m pip install --disable-pip-version-check --no-index --find-links $wheels --target "$work\deps" -r "$root\automation\validation-requirements.txt" Pillow==13.0.0 > "$out\offline-deps.log" 2>&1
    if ($LASTEXITCODE) { throw 'Offline Pillow install failed' }
    # Keep Vitest/Vite transient files in this snapshot. pnpm links are preserved.
    & robocopy.exe $sourceModules "$root\web\frontend\node_modules" /E /SL /SJ /R:1 /W:1 /XD .vite .vite-temp > "$out\node-copy.log"
    if ($LASTEXITCODE -ge 8) { throw 'Guest dependency copy failed' }
    $env:PR11C_VMWARE_GUEST = '1'
    $env:PYTHONIOENCODING = 'utf8'
    Set-Location -LiteralPath $root
    & $python -I -B tools/pr11c/run_guest.py --suite $Suite --pg-bin $pg --weed $weed --node $node --dependency-root $old --deps "$work\deps" > "$out\runner.log"
    $code = $LASTEXITCODE
} catch {
    $_.ToString() | Set-Content -LiteralPath "$out\guest-setup.log" -Encoding UTF8
} finally {
    $physicalRoot = "C:\j\class-assistant\worktrees\$SnapshotTag"
    $remaining = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^(python|node|chrome|postgres|pg_ctl|weed)\.exe$' -and
        ($_.CommandLine -like "*$root*" -or $_.CommandLine -like "*$physicalRoot*")
    } | Select-Object Name, ProcessId)
    if ($remaining.Count) { $code = 1 }
    @{ count = $remaining.Count; remaining_owned_processes = $remaining; exit_code = $code } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath "$out\cleanup.json" -Encoding UTF8
    $evidence = "$out\evidence.zip"
    $files = @(Get-ChildItem -LiteralPath $out -File | Where-Object Extension -In '.json', '.log', '.xml' | Select-Object -ExpandProperty FullName)
    Compress-Archive -LiteralPath $files -DestinationPath $evidence
    Write-Output "evidence=$evidence sha256=$((Get-FileHash -LiteralPath $evidence).Hash.ToLowerInvariant()) exit=$code"
}
exit $code
