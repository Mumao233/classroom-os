# Runs inside the guest (Windows PowerShell 5.1). Mirrors the read-only shared worktree to
# local disk at the same J:\ path the host uses (via subst), recreates dependency links and
# points the task venv at the copied base Python. Runs no tests.
$ErrorActionPreference = 'Stop'
$share = '\\vmware-host\Shared Folders\pr03b-src'
$rel = 'class-assistant\worktrees\web-pr03b-sessions'
$local = "C:\j\$rel"
$W = 'work\pr03b\product-completion-20261110-01'
$started = Get-Date
if (-not (Test-Path -LiteralPath $share)) { throw 'Shared folder is not visible.' }
$null = New-Item -ItemType Directory -Path $local -Force
$failed = New-Object Collections.Generic.List[string]

function Sync([string]$Path, [string]$Mode, [string[]]$ExcludeDirs = @(), [string[]]$Extra = @(), [string]$From = '') {
    if (-not $From) { $From = Join-Path $share $Path }
    $arguments = @($From, (Join-Path $local $Path), $Mode, '/R:1', '/W:1', '/MT:8', '/XJ', '/NFL', '/NDL', '/NJH', '/NJS', '/NP') + $Extra
    if ($ExcludeDirs.Count) { $arguments += '/XD'; $arguments += $ExcludeDirs }
    $output = & robocopy.exe @arguments
    if ($LASTEXITCODE -ge 8) { $failed.Add("$Path exit=$LASTEXITCODE"); $output | Select-Object -Last 6 | ForEach-Object { "    $_" } }
}

# Source trees: exact mirror, so deletions on the host propagate.
Sync 'web' '/MIR' @((Join-Path $share 'web\frontend\node_modules'), '__pycache__', '.pytest_cache', (Join-Path $share 'web\backend\v'), (Join-Path $local 'web\frontend\node_modules'))
Sync 'tools' '/MIR' @('__pycache__')
Sync 'docs' '/MIR'
Sync '.github' '/MIR'
$null = & robocopy.exe $share $local /LEV:1 /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XF *.vbs
if ($LASTEXITCODE -ge 8) { $failed.Add("root files exit=$LASTEXITCODE") }
"sources synced: $([int]((Get-Date)-$started).TotalSeconds)s"

# Toolchains: additive copy.
Sync "$W\venv" '/E' @('__pycache__')
Sync 'work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec' '/E'
Sync 'work\pr09a\dist' '/MIR'
Sync 'work\pr03b\vmware-broker\payload' '/E'
# The 53b5 PostgreSQL bundle lacks iconv.dll/charset.dll, which libxml2.dll imports; on the
# host they were evidently picked up from the ambient PATH. Take them from the sibling
# 7409 bundle of the same PostgreSQL 17.11 build so the guest copy is self-contained.
$pgLocal = Join-Path $local 'work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec\runtime\Library\bin'
foreach ($name in 'iconv.dll', 'charset.dll') {
    if (-not (Test-Path -LiteralPath (Join-Path $pgLocal $name))) {
        Copy-Item -LiteralPath (Join-Path $share "work\pr03b\toolchains\pg17.11-win64-7409a40dc2bda548\runtime\Library\bin\$name") -Destination $pgLocal
        "  added $name to the guest PostgreSQL bundle"
    }
}
"toolchains synced:$([int]((Get-Date)-$started).TotalSeconds)s"

# Frontend dependencies. The share shows links as plain directories, so copy only real
# directories (excluding every link path) and recreate the links afterwards.
$manifest = Get-Content -LiteralPath (Join-Path $local 'work\pr03b\vmware-broker\payload\links.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$links = @($manifest.links)
$nm = 'web\frontend\node_modules'
Sync $nm '/E' @($links | Where-Object { $_.path.StartsWith("$nm\") } | ForEach-Object { Join-Path $share $_.path })
$store = "$W\frontend_modules\.pnpm"
# The shared folder rejects paths near 260 characters, so read the store through a short
# drive letter mapped straight onto it.
$null = & subst.exe S: /D 2>$null
$null = & subst.exe S: (Join-Path $share $store)
if (-not (Test-Path 'S:\')) { throw 'Could not map S: onto the shared dependency store.' }
$byEntry = @{}
foreach ($link in $links) {
    if ($link.path.StartsWith("$store\")) {
        $inside = $link.path.Substring($store.Length + 1)
        $entry = $inside.Split('\')[0]
        if (-not $byEntry.ContainsKey($entry)) { $byEntry[$entry] = New-Object Collections.Generic.List[string] }
        $byEntry[$entry].Add("S:\$inside")
    }
}
$null = New-Item -ItemType Directory -Path (Join-Path $local $store) -Force
$null = & robocopy.exe 'S:\' (Join-Path $local $store) /LEV:1 /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
$entries = @(Get-ChildItem -LiteralPath 'S:\' -Directory -Force)
$linkOnly = 0
foreach ($entry in $entries) {
    $exclude = if ($byEntry.ContainsKey($entry.Name)) { $byEntry[$entry.Name].ToArray() } else { @() }
    if ((($exclude -join ' ').Length) -gt 20000) {
        # A directory made only of links (pnpm's hoisting folder): nothing real to copy,
        # and the exclusion list would exceed the Windows command-line limit.
        $linkOnly++; $null = New-Item -ItemType Directory -Path (Join-Path $local "$store\$($entry.Name)") -Force; continue
    }
    Sync "$store\$($entry.Name)" '/E' $exclude @() "S:\$($entry.Name)"
}
$null = & subst.exe S: /D
"link-only store entries skipped: $linkOnly"

# Files too long for the shared folder arrive packed; extract them through a short drive.
$longZip = Join-Path $local 'work\pr03b\vmware-broker\payload\longpaths.zip'
$null = & subst.exe T: /D 2>$null
$null = & subst.exe T: (Join-Path $local $store)
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($longZip); $unpacked = 0
try {
    foreach ($item in $archive.Entries) {
        $target = 'T:\' + $item.FullName.Replace('/', '\')
        $null = New-Item -ItemType Directory -Path (Split-Path $target) -Force
        [IO.Compression.ZipFileExtensions]::ExtractToFile($item, $target, $true); $unpacked++
    }
} finally { $archive.Dispose(); $null = & subst.exe T: /D }
"long-path files unpacked: $unpacked"
# Entries that failed only on those long files are now complete.
$stillFailed = @($failed | Where-Object { $_ -notmatch '\\frontend_modules\\\.pnpm\\' })
$storeFailures = $failed.Count - $stillFailed.Count
$failed.Clear(); foreach ($f in $stillFailed) { $failed.Add($f) }
"store entries with long-path copy errors (covered by the archive): $storeFailures"

# App-local Visual C++ runtime for the PostgreSQL bundle when the guest lacks it.
$pgBin = Join-Path $local 'work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec\runtime\Library\bin'
foreach ($dll in Get-ChildItem -LiteralPath (Join-Path $local 'work\pr03b\vmware-broker\payload\vcruntime') -File) {
    if (-not (Test-Path -LiteralPath (Join-Path $env:WINDIR "System32\$($dll.Name)")) -and -not (Test-Path -LiteralPath (Join-Path $pgBin $dll.Name))) {
        Copy-Item -LiteralPath $dll.FullName -Destination $pgBin; "  added $($dll.Name) next to postgres.exe"
    }
}
"dependency files synced ($($entries.Count) store entries): $([int]((Get-Date)-$started).TotalSeconds)s"

$made = 0; $kept = 0; $bad = 0
foreach ($link in $links) {
    $path = Join-Path $local $link.path; $target = Join-Path $local $link.target
    $existing = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)) { $kept++; continue }
    if ($existing) { Remove-Item -LiteralPath $path -Recurse -Force }
    if (-not (Test-Path -LiteralPath $target)) { $bad++; if ($bad -le 5) { "  missing link target: $($link.target)" }; continue }
    $null = New-Item -ItemType Directory -Path (Split-Path $path) -Force
    $null = & cmd.exe /c mklink /J "$path" "$target" 2>&1
    if ($LASTEXITCODE -eq 0) { $made++ } else { $bad++; if ($bad -le 5) { "  mklink failed: $($link.path)" } }
}
"links: created=$made kept=$kept failed=$bad of $($links.Count)"
if ($bad) { $failed.Add("links failed=$bad") }

# Same absolute paths as the host, without needing a real J: volume.
if (-not (Test-Path 'J:\')) { $null = & subst.exe J: C:\j }
$root = "J:\$rel"
$cfg = Join-Path $root "$W\venv\pyvenv.cfg"
$text = (Get-Content -LiteralPath $cfg) | ForEach-Object { if ($_ -match '^home\s*=') { "home = $root\work\pr03b\vmware-broker\payload\python-base" } else { $_ } }
[IO.File]::WriteAllLines($cfg, $text)
foreach ($d in "$W\tmp", "$W\cache", 'outputs\pr03b\product-completion-20261110-01', 'outputs\pr03a\third-round') { $null = New-Item -ItemType Directory -Path (Join-Path $root $d) -Force }

"--- verification"
$python = Join-Path $root "$W\venv\Scripts\python.exe"
& $python -I -B -c "import sys,psycopg,argon2,fastapi,pytest;print('python',sys.version.split()[0],'psycopg',psycopg.__version__,'pytest',pytest.__version__,'prefix',sys.prefix)"
"python_exit=$LASTEXITCODE"
$nodeExe = Join-Path $root 'work\pr03b\vmware-broker\payload\node\node.exe'
Push-Location (Join-Path $root 'web\frontend')
& $nodeExe -e "for (const m of ['vitest/package.json','typescript/package.json','vite/package.json','eslint/package.json','@playwright/test/package.json']) console.log(m, require(require.resolve(m)).version); console.log('node', process.version)"
"node_exit=$LASTEXITCODE"
Pop-Location
& (Join-Path $root 'work\pr03b\toolchains\pg17.11-win64-53b5364a5a04e8ec\runtime\Library\bin\postgres.exe') --version
"postgres_exit=$LASTEXITCODE"
"free_gb=$([math]::Round((Get-PSDrive C).Free/1GB,1)) total_seconds=$([int]((Get-Date)-$started).TotalSeconds)"
if ($failed.Count) { "FAILED: " + ($failed -join '; '); exit 1 }
"sync ok"
exit 0
