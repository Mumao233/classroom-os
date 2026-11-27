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

# The pathname checks above can race with a directory being swapped for a junction. Each file is
# therefore opened once, the final path of that open handle is checked against the worktree's own
# final path, and the hash and archive bytes are read from the same handle.
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class Pr11cFinalPath {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder path, uint size, uint flags);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security,
                                             uint disposition, uint flags, IntPtr template);
    public static string OfHandle(SafeFileHandle handle) {
        var path = new StringBuilder(32768);
        uint length = GetFinalPathNameByHandleW(handle, path, (uint)path.Capacity, 0);
        if (length == 0 || length >= path.Capacity) throw new Win32Exception(Marshal.GetLastWin32Error());
        return path.ToString();
    }
    public static string OfDirectory(string directory) {
        // FILE_READ_ATTRIBUTES, share read/write/delete, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS
        using (var handle = CreateFileW(directory, 0x80, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            return OfHandle(handle);
        }
    }
}
'@
$rootFinal = [Pr11cFinalPath]::OfDirectory($root).TrimEnd('\') + '\'

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
        $input = [IO.File]::Open($source.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {
            $final = [Pr11cFinalPath]::OfHandle($input.SafeFileHandle)
            if (-not $final.StartsWith($rootFinal, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Opened source resolves outside the worktree: $file"
            }
            $sha256 = [Security.Cryptography.SHA256]::Create()
            try { $hashes[$file] = [Convert]::ToHexString($sha256.ComputeHash($input)).ToLowerInvariant() }
            finally { $sha256.Dispose() }
            $input.Position = 0
            $entry = $zip.CreateEntry($file.Replace('\','/'), [IO.Compression.CompressionLevel]::Optimal)
            $output = $entry.Open()
            try { $input.CopyTo($output) } finally { $output.Dispose() }
        } finally {
            $input.Dispose()
        }
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
