# Parameters are supplied as variables by the host submitter. VMware runs this in Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'
if (-not (Test-Path 'J:\')) { $null = & subst.exe J: C:\j }
$root = 'J:\class-assistant\worktrees\web-pr04a-storage'
$base = 'J:\class-assistant\worktrees\web-pr03b-sessions'
$work = Join-Path $root 'work\pr04a'
$null = New-Item -ItemType Directory -Force -Path $work
$snapshot = Join-Path $work $SnapshotTag
$null = New-Item -ItemType Directory -Force -Path $snapshot
$share = '\\vmware-host\Shared Folders\pr03b-src\work\pr03b\vmware-broker'
Copy-Item -LiteralPath (Join-Path $share "$SnapshotTag\payload.zip") -Destination "$snapshot\payload.zip"
Expand-Archive -LiteralPath "$snapshot\payload.zip" -DestinationPath "$snapshot\payload"
Expand-Archive -LiteralPath "$snapshot\payload\source.zip" -DestinationPath $root -Force
if (-not (Test-Path "$work\seaweedfs\weed.exe")) {
    Expand-Archive -LiteralPath "$snapshot\payload\seaweedfs.zip" -DestinationPath "$work\seaweedfs"
}
if (-not (Test-Path "$work\venv\Scripts\python.exe")) {
    $null = & robocopy.exe "$base\work\pr03b\product-completion-20261110-01\venv" "$work\venv" /E /XJ /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { throw 'isolated venv preparation failed' }
}
$python = "$work\venv\Scripts\python.exe"
$env:TEMP = $work
$env:TMP = $work
& $python -I -B -m pip install --no-index --find-links "$snapshot\payload\wheels" 'boto3==1.44.3' 'python-dateutil==2.9.0.post0' 'urllib3==2.8.0'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -I -B -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Set-Location -LiteralPath $root
& $python -I -B tools/pr04a/verify_snapshot.py "$snapshot\payload\SOURCE.json"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($TestMode -eq 'full') {
    # Read-only reuse of the already prepared frontend toolchain and unchanged PR-09a build.
    foreach ($pair in @(
        @('web\frontend\node_modules', 'web\frontend\node_modules'),
        @('work\pr09a\dist', 'work\pr09a\dist'),
        @('work\pr03b\vmware-broker\payload', 'work\pr03b\vmware-broker\payload')
    )) {
        $destination = Join-Path $root $pair[0]
        if (-not (Test-Path $destination)) {
            $null = New-Item -ItemType Directory -Force -Path (Split-Path $destination)
            $null = New-Item -ItemType Junction -Path $destination -Target (Join-Path $base $pair[1])
        }
    }
    $env:PR03B_REAL_BROWSER = '1'
}
& $python -I -B tools/pr04a/run_guest.py $TestMode
exit $LASTEXITCODE
