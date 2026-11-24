#requires -Version 7.0
[CmdletBinding()]
param([ValidateSet('targeted', 'full')][string]$Suite = 'full')

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$broker = 'J:\class-assistant\worktrees\web-pr03b-sessions\work\pr03b\vmware-broker'
$tag = 'pr11c-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$work = Join-Path $root "work/pr11c/vmware/$tag"
$transfer = Join-Path $broker $tag
$base = & git -C $root rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Base SHA could not be read' }
$baseline = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$headFiles = @(& git -c core.quotepath=false -C $root ls-tree -r --name-only $base)
if ($LASTEXITCODE -ne 0) { throw 'HEAD source enumeration failed' }
foreach ($path in $headFiles) { [void]$baseline.Add($path) }
$tracked = @(& git -c core.quotepath=false -C $root ls-files --cached)
if ($LASTEXITCODE -ne 0) { throw 'Tracked source enumeration failed' }
$untracked = @(& git -c core.quotepath=false -C $root ls-files --others --exclude-standard)
if ($LASTEXITCODE -ne 0) { throw 'Untracked source enumeration failed' }
$allowed = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($path in (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'pending-source-files.txt') -Encoding UTF8)) {
    if ($path) { [void]$allowed.Add($path) }
}
$files = $tracked + $untracked
$unexpected = @($files | Where-Object { -not $baseline.Contains($_) -and -not $allowed.Contains($_) })
if ($unexpected.Count) { throw "Unlisted paths outside HEAD. Archive refused: $($unexpected -join ', ')" }
$branch = & git -C $root branch --show-current
if ($LASTEXITCODE -ne 0) { throw 'Branch could not be read' }

# Before any file is opened, reject reparse points on every directory between the worktree
# root and each source file, and any path that resolves outside the root: a junction must not
# pull content from outside the reviewed checkout into the guest archive.
$rootFull = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
$checkedDirs = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($file in ($files | Sort-Object -Unique)) {
    $full = [IO.Path]::GetFullPath((Join-Path $root $file))
    if (-not $full.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Source path resolves outside the worktree: $file"
    }
    $parts = $file -split '/'
    $cursor = $root
    for ($i = 0; $i -lt $parts.Count - 1; $i++) {
        $cursor = Join-Path $cursor $parts[$i]
        if ($checkedDirs.Add($cursor)) {
            $dir = Get-Item -LiteralPath $cursor -Force
            if (-not $dir.PSIsContainer -or ($dir.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Reparse point or non-directory on a source path: $file"
            }
        }
    }
}

$null = New-Item -ItemType Directory -Path $work, $transfer
Add-Type -AssemblyName System.IO.Compression
$archive = Join-Path $work 'source.zip'
$hashes = [ordered]@{}
$stream = [IO.File]::Open($archive, [IO.FileMode]::CreateNew)
$zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($file in ($files | Sort-Object -Unique)) {
        $source = Get-Item -LiteralPath (Join-Path $root $file)
        if ($source.PSIsContainer -or ($source.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Unexpected source entry $file"
        }
        $hashes[$file] = (Get-FileHash -LiteralPath $source.FullName).Hash.ToLowerInvariant()
        $entry = $zip.CreateEntry($file.Replace('\','/'), [IO.Compression.CompressionLevel]::Optimal)
        $input = $source.OpenRead()
        $output = $entry.Open()
        try { $input.CopyTo($output) } finally { $output.Dispose(); $input.Dispose() }
    }
} finally {
    $zip.Dispose()
    $stream.Dispose()
}
$sha = (Get-FileHash -LiteralPath $archive).Hash.ToLowerInvariant()
$manifest = Join-Path $work 'SOURCE.json'
[ordered]@{ base_sha = $base; branch = $branch; source_zip_sha256 = $sha; files_sha256 = $hashes } |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifest -Encoding utf8NoBOM
Copy-Item -LiteralPath $archive, $manifest -Destination $transfer

$script = Join-Path $broker "scripts/$tag.ps1"
$preamble = @'
$SnapshotTag = '{0}'
$Suite = '{1}'
$SourceHash = '{2}'
'@ -f $tag, $Suite, $sha
$guest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'guest.ps1') -Raw -Encoding UTF8
Set-Content -LiteralPath $script -Value ($preamble + "`r`n" + $guest) -Encoding utf8BOM
$job = [ordered]@{
    id = $tag
    script = $script
    guest_evidence = "C:\j\class-assistant\worktrees\$tag\outputs\pr11c\evidence.zip"
    source_zip_sha256 = $sha
    work = $work
    suite = $Suite
}
$receipt = Join-Path $work 'job.json'
$job | ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding utf8NoBOM
Get-Content -LiteralPath $receipt
