#requires -Version 7.0
<#
  Host-side, static preparation for guest test runs. Runs no tests.
  - Copies the base Python the task venv was created from (stdlib only) and node.exe
    into work/pr03b/vmware-broker/payload so the read-only share exposes them.
  - Writes links.json: every directory link under the frontend dependency trees,
    because the VMware shared folder shows links as plain directories and the guest
    has to recreate them.
#>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
$payload = Join-Path $repo 'work\pr03b\vmware-broker\payload'
$null = New-Item -ItemType Directory -Path $payload -Force

$venv = Join-Path $repo 'work\pr03b\product-completion-20261110-01\venv'
$home_ = ((Get-Content -LiteralPath (Join-Path $venv 'pyvenv.cfg')) -match '^home\s*=')[0] -replace '^home\s*=\s*', ''
if (-not (Test-Path -LiteralPath (Join-Path $home_ 'python.exe'))) { throw "Base Python not found at venv home." }
$pythonTarget = Join-Path $payload 'python-base'
robocopy $home_ $pythonTarget /E /XD site-packages __pycache__ /NFL /NDL /NJH /NP /R:1 /W:1 | Select-Object -Last 8
if ($LASTEXITCODE -ge 8) { throw "robocopy python-base failed ($LASTEXITCODE)" }
$null = New-Item -ItemType Directory -Path (Join-Path $pythonTarget 'Lib\site-packages') -Force

$node = (Get-Command node.exe -CommandType Application | Select-Object -First 1).Source
$nodeTarget = Join-Path $payload 'node'
$null = New-Item -ItemType Directory -Path $nodeTarget -Force
Copy-Item -LiteralPath $node -Destination (Join-Path $nodeTarget 'node.exe') -Force

$links = [Collections.Generic.List[object]]::new()
function Walk([string]$Directory) {
    foreach ($child in [IO.Directory]::EnumerateDirectories($Directory)) {
        $info = [IO.DirectoryInfo]::new($child)
        if ($info.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            $target = $info.LinkTarget
            if (-not $target) { throw "Unreadable link: $child" }
            if (-not [IO.Path]::IsPathRooted($target)) { $target = Join-Path $info.Parent.FullName $target }
            $target = [IO.Path]::GetFullPath($target) -replace '^\\\\\?\\', ''
            if (-not $target.StartsWith($repo + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Link leaves the worktree: $child -> $target" }
            $links.Add([pscustomobject]@{ path = $child.Substring($repo.Length + 1); target = $target.Substring($repo.Length + 1) })
        } else { Walk $child }
    }
}
$roots = @('web\frontend\node_modules', 'work\pr03b\product-completion-20261110-01\frontend_modules')
foreach ($root in $roots) { Walk (Join-Path $repo $root) }
$longest = ($links | ForEach-Object { [Math]::Max($_.path.Length, $_.target.Length) } | Measure-Object -Maximum).Maximum
[pscustomobject]@{ repo = $repo; roots = $roots; links = $links } | ConvertTo-Json -Depth 4 -Compress | Set-Content -LiteralPath (Join-Path $payload 'links.json') -Encoding utf8

# VMware's shared folder cannot serve host files whose full path is about 260 characters
# or longer. Pack those few dependency files, relative to the pnpm store, for the guest.
$storeRoot = Join-Path $repo 'work\pr03b\product-completion-20261110-01\frontend_modules\.pnpm'
$longZip = Join-Path $payload 'longpaths.zip'
if (Test-Path -LiteralPath $longZip) { Remove-Item -LiteralPath $longZip -Force }
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$longFiles = @([IO.Directory]::EnumerateFiles($storeRoot, '*', [IO.EnumerationOptions]@{ RecurseSubdirectories = $true; AttributesToSkip = [IO.FileAttributes]::ReparsePoint }) | Where-Object { $_.Length -ge 240 })
$zip = [IO.Compression.ZipFile]::Open($longZip, [IO.Compression.ZipArchiveMode]::Create)
try { foreach ($file in $longFiles) { $null = [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file, $file.Substring($storeRoot.Length + 1).Replace('\', '/')) } } finally { $zip.Dispose() }
$outside = @(foreach ($root in $roots) { [IO.Directory]::EnumerateFiles((Join-Path $repo $root), '*', [IO.EnumerationOptions]@{ RecurseSubdirectories = $true; AttributesToSkip = [IO.FileAttributes]::ReparsePoint }) | Where-Object { $_.Length -ge 240 -and -not $_.StartsWith($storeRoot + '\') } })
"long-path files packed: $($longFiles.Count); long-path files outside the store (not packed): $($outside.Count)"

# The PostgreSQL bundle needs the Visual C++ runtime, which the guest may not have installed.
$vc = Join-Path $payload 'vcruntime'
$null = New-Item -ItemType Directory -Path $vc -Force
Get-ChildItem -LiteralPath "$env:WINDIR\System32" -File | Where-Object { $_.Name -match '^(msvcp140.*|vcruntime140.*|concrt140|vcomp140)\.dll$' } | Copy-Item -Destination $vc -Force
"vc runtime dlls: " + ((Get-ChildItem -LiteralPath $vc -Name) -join ', ')

$pySize = (Get-ChildItem -LiteralPath $pythonTarget -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
"python-base: {0:N0} MB from {1}" -f $pySize, $home_
"node: $(& (Join-Path $nodeTarget 'node.exe') --version)"
"links: $($links.Count); longest relative path: $longest chars (+ $($repo.Length + 1) prefix)"
