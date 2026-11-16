# Windows PowerShell 5.1, executed only through the existing VMware broker.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Archive,
    [Parameter(Mandatory)][string]$RunId,
    [Parameter(Mandatory)][string]$ArchiveSha256
)
$ErrorActionPreference = 'Stop'
if ($RunId -notmatch '^pr05a-[a-zA-Z0-9-]+$') { throw 'Invalid run id' }
$machine = Get-CimInstance Win32_ComputerSystem
if ($machine.Manufacturer -notmatch 'VMware' -and $machine.Model -notmatch 'VMware') { throw 'VMware guest required' }
if (-not (Test-Path 'J:\')) { & subst.exe J: C:\j | Out-Null }
$taskRoot = 'J:\class-assistant\worktrees\web-pr05a-cache-security'
$root = Join-Path $taskRoot "work\pr05a\$RunId"
if (Test-Path -LiteralPath $root) { throw 'Run directory already exists' }
New-Item -ItemType Directory -Path $root -Force | Out-Null
$copy = Join-Path $root 'source.zip'
Copy-Item -LiteralPath $Archive -Destination $copy
if ((Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash -ne $ArchiveSha256) { throw 'Source archive mismatch' }
Expand-Archive -LiteralPath $copy -DestinationPath (Join-Path $root 'source')
$dependencyRoot = 'J:\class-assistant\worktrees\web-pr03b-sessions'
$python = Join-Path $dependencyRoot 'work\pr03b\product-completion-20261110-01\venv\Scripts\python.exe'
$node = Join-Path $dependencyRoot 'work\pr03b\vmware-broker\payload\node\node.exe'
$playwright = Join-Path $dependencyRoot 'web\frontend\node_modules\@playwright\test\index.mjs'
$browsers = Join-Path $dependencyRoot 'work\pr03b\vmware-broker\payload\ms-playwright'
foreach ($path in @($python, $node, $playwright, $browsers)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing guest dependency $path" }
}
$env:PR05A_VMWARE_GUEST = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = Join-Path $root 'temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$runner = Join-Path $root 'source\run_experiment.py'
$evidence = Join-Path $root 'evidence'
"vm_manufacturer=$($machine.Manufacturer) model=$($machine.Model) run_id=$RunId archive_sha256=$ArchiveSha256"
& $python -B $runner --run-dir $evidence --node $node --playwright-module $playwright --browser-path $browsers
$code = $LASTEXITCODE
$deliverables = @('results.json', 'service-tests.log', 'browser-tests.log', 'browser-results.json', 'experiment-ui.png') |
    ForEach-Object { Join-Path $evidence $_ } | Where-Object { Test-Path -LiteralPath $_ }
if ($deliverables.Count) { Compress-Archive -LiteralPath $deliverables -DestinationPath (Join-Path $root 'evidence.zip') }
"guest_exit=$code evidence_archive=$(Join-Path $root 'evidence.zip')"
exit $code
