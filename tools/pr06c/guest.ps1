# Enter through the existing VMware broker. Validate the exact Git bundle.
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{40}$')][string]$Commit,
    [Parameter(Mandatory)][ValidateSet('targeted','full')][string]$Mode,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{16}$')][string]$RunId,
    [Parameter(Mandatory)][ValidatePattern('^[A-Fa-f0-9]{64}$')][string]$BundleHash
)
$ErrorActionPreference = 'Stop'
$machine = Get-CimInstance Win32_ComputerSystem
if ($machine.Manufacturer -notmatch 'VMware' -and $machine.Model -notmatch 'VMware') { throw 'VMware guest required' }
if (-not (Test-Path -LiteralPath 'J:\')) { & subst.exe J: C:\j; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
$primary = 'J:/class-assistant/worktrees/web-pr03b-sessions'
$media = 'J:/class-assistant/worktrees/web-pr04a-storage'
$git = 'J:/class-assistant/worktrees/pr03b-integration-20261114-01/work/pr03b/integration-20261114-01/git/cmd/git.exe'
$node = "$primary/work/pr03b/vmware-broker/payload/node/node.exe"
$python = "$media/work/pr04a/venv/Scripts/python.exe"
$share = '\\vmware-host\Shared Folders\pr03b-src\work\pr03b\vmware-broker'
$wheels = "$share\pr08c-media-gate-r0-0b289f8\validation-wheels"
$legacyWheels = "$share\pr08c-closeout-head-legacy\legacy-wheels"
$root = "J:/class-assistant/worktrees/pr06c-$RunId"
$work = "$root/work/pr06c"
$out = "$root/outputs/pr06c"
$bundle = "$share\scripts\pr06c-$RunId.bundle"
if (Test-Path -LiteralPath $root) { throw 'Run directory already exists' }
if ((Get-FileHash -LiteralPath $bundle -Algorithm SHA256).Hash -ne $BundleHash) { throw 'Bundle hash mismatch' }
& $git clone --quiet --branch feature/web-pr06c-filetrans --single-branch $bundle $root
if ($LASTEXITCODE -or (& $git -C $root rev-parse HEAD) -ne $Commit -or (& $git -C $root status --porcelain)) {
    throw 'Source checkout does not match the requested clean commit'
}
$null = New-Item -ItemType Directory -Path $work,$out,"$work/guard","$work/objects","$work/tmp"
$env:TEMP = $env:TMP = "$work/tmp"
$env:PIP_CACHE_DIR = "$work/pip-cache"
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:PR06C_VMWARE_GUEST = '1'
$env:AWS_EC2_METADATA_DISABLED = 'true'
$env:PR04B_DELETION_INTEGRATION = '1'
$env:PR05B_CAPTURE_INTEGRATION = '1'
$env:PR08B_SYNC_INTEGRATION = '1'
$env:PR08B_VMWARE_GUEST = '1'
$env:PR08B_DEPENDENCY_ROOT = $primary
$env:PR04B_OBJECT_ROOT = "$work/objects"
$env:PR04B_WEED = "$media/work/pr04a/seaweedfs/weed.exe"
$env:PR04B_PG_BIN = "$primary/work/pr03b/toolchains/pg17.11-win64-53b5364a5a04e8ec/runtime/Library/bin"
$env:PLAYWRIGHT_BROWSERS_PATH = "$primary/work/pr03b/vmware-broker/payload/ms-playwright"
$env:Path = (Split-Path $git) + ';' + (Split-Path $node) + ';' + $env:Path
$report = [ordered]@{commit=$Commit; bundle_sha256=$BundleHash; suite=$Mode; provider='synthetic offline only';
    guest_os=[Environment]::OSVersion.VersionString; guest_model=$machine.Model; steps=[ordered]@{}}
function Record($name, $code) {
    $report.steps[$name] = [int]$code
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath "$out/results.json" -Encoding UTF8
    Write-Output "$name exit=$code"
}
function Gate($name, [scriptblock]$run) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE=$null
        & $run *> "$out/$name.log"
        Record $name $(if($null -eq $LASTEXITCODE){1}else{$LASTEXITCODE})
    } finally { $ErrorActionPreference=$previous }
}
Set-Location -LiteralPath $root
Gate 'offline-deps' { & $python -I -X utf8 -B -m pip install --disable-pip-version-check --no-index --find-links $wheels --target "$work/deps" -r automation/validation-requirements.txt Pillow==13.0.0 }
Gate 'loopback-guard' { & $python -I -X utf8 -B tools/pr06c/write_guard.py "$work/guard/sitecustomize.py" }
if ($report.steps['offline-deps'] -eq 0 -and $report.steps['loopback-guard'] -eq 0) {
    if ($Mode -eq 'targeted') {
        $env:PYTHONPATH = "$work/deps"
        Gate 'targeted' { & $python -X utf8 -B tools/pr06c/run_targeted.py --deps "$work/deps" --guard "$work/guard/sitecustomize.py" --output "$out/targeted" }
        Remove-Item Env:PYTHONPATH
    } else {
        $front = Join-Path $root 'web/frontend'
        $null = New-Item -ItemType Junction -Path "$front/node_modules" -Target "$primary/web/frontend/node_modules"
        Gate 'frozen-contracts' { & $python -I -X utf8 -B tools/overnight/check_contracts.py --deps "$work/deps" --output "$out/contracts.json" }
        Gate 'web-backend-full' { & $python -I -X utf8 -B tools/pr07b/run_combined.py --root $root --deps "$work/deps" --auth-browser --pytest-loopback-guard "$work/guard/sitecustomize.py" --timeout 2100 }
    }
} else { Record $Mode 124 }
if ($Mode -eq 'full') {
    Gate 'legacy-venv' { & $python -I -X utf8 -B -m venv --without-pip "$work/legacy" }
    $legacy = "$work/legacy/Scripts/python.exe"
    if ($report.steps['legacy-venv'] -eq 0) {
        Gate 'legacy-deps' { & $python -I -X utf8 -B -m pip --python $legacy install --disable-pip-version-check --no-index --find-links $legacyWheels -r requirements-dev.txt -c tools/pr00/constraints-py310-windows.txt }
        if ($report.steps['legacy-deps'] -eq 0) {
            Gate 'legacy-full' { & $legacy -X utf8 -B tools/pr00/run_checks.py --node $node --powershell powershell.exe --output-dir "$root/work/pr00/pr06c-$RunId" }
        } else { Record 'legacy-full' 124 }
    } else { Record 'legacy-full' 124 }
}
& $git -C $root diff --exit-code --no-ext-diff HEAD -- > "$out/source-clean.log"
Record 'source-clean' $(if($LASTEXITCODE -or (& $git -C $root status --porcelain)){1}else{0})
foreach ($path in @("$root/outputs/pr07b/combined","$root/work/pr00/pr06c-$RunId")) {
    if (Test-Path -LiteralPath $path) {
        $target = Join-Path $out (Split-Path $path -Leaf)
        $null = New-Item -ItemType Directory -Path $target
        $prefix = (Get-Item -LiteralPath $path).FullName
        Get-ChildItem -LiteralPath $path -Recurse -File |
            Where-Object { $_.Extension -in '.json','.xml','.log','.md' -and $_.Length -le 20000000 } |
            ForEach-Object {
                $dest = Join-Path $target $_.FullName.Substring($prefix.Length+1)
                $null = New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force
                Copy-Item -LiteralPath $_.FullName -Destination $dest
            }
    }
}
Compress-Archive -Path "$out/*" -DestinationPath "$work/evidence.zip"
"evidence_path=$work/evidence.zip"
"evidence_sha256=$((Get-FileHash -LiteralPath "$work/evidence.zip" -Algorithm SHA256).Hash)"
if ($Mode -eq 'targeted' -and (Test-Path "$out/targeted/pytest.log")) {
    Get-Content -LiteralPath "$out/targeted/pytest.log" -Tail 65
}
if (@($report.steps.Values | Where-Object { $_ -ne 0 }).Count) { exit 1 }
exit 0
