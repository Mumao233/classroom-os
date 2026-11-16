#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('Targeted', 'Full')][string]$Mode = 'Targeted',
    [string[]]$TestTarget = @(),
    [ValidateRange(120, 3600)][int]$TimeoutSeconds = 2400
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$job = & (Join-Path $root 'tools\pr07b\prepare_vmware.ps1') -Mode $Mode -TestTarget $TestTarget | ConvertFrom-Json
$output = Join-Path $root "outputs\pr07b\$($job.id)"
$null = New-Item -ItemType Directory -Path $output
Copy-Item -LiteralPath (Join-Path $job.work 'SOURCE.json'), (Join-Path $job.work 'job.json') -Destination $output
Write-Output "job=$($job.id) mode=$Mode output=$output"
& $job.submit -Id $job.id -Script $job.script -TimeoutSeconds $TimeoutSeconds -TailLines 100
$testExit = $LASTEXITCODE
$broker = Split-Path -Parent $job.transfer
$recordPath = Join-Path $broker "done\$($job.id).result.json"
if (-not (Test-Path -LiteralPath $recordPath)) {
    throw 'Broker has no terminal result. Inspect the original job before any retry.'
}
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
Copy-Item -LiteralPath $recordPath -Destination (Join-Path $output 'broker-result.json')
if ($record.Result -and $record.Result.StdOutPath) {
    Copy-Item -LiteralPath $record.Result.StdOutPath -Destination (Join-Path $output 'broker-stdout.txt')
    Copy-Item -LiteralPath $record.Result.StdErrPath -Destination (Join-Path $output 'broker-stderr.txt')
}
if ($record.BrokerError -or -not $record.Result.TransportSucceeded) {
    throw 'Guest transport did not complete. Inspect the original job before any retry.'
}
$copyId = $job.id + '-evidence'
& $job.submit -Id $copyId -Kind copyFrom -GuestPath $job.evidence_guest -TimeoutSeconds 180
$copyPath = Join-Path $broker "done\$copyId.result.json"
$copy = Get-Content -LiteralPath $copyPath -Raw | ConvertFrom-Json
if ($copy.BrokerError -or -not $copy.Result.Succeeded) { throw 'Evidence transport failed.' }
Copy-Item -LiteralPath $copyPath -Destination (Join-Path $output 'evidence-transfer.json')
Copy-Item -LiteralPath $copy.Result.HostPath -Destination (Join-Path $output 'evidence.zip')
$evidenceHash = (Get-FileHash -LiteralPath (Join-Path $output 'evidence.zip') -Algorithm SHA256).Hash.ToLowerInvariant()
$stdout = Get-Content -LiteralPath (Join-Path $output 'broker-stdout.txt') -Raw
if (-not $stdout.Contains($evidenceHash)) {
    throw 'Recovered evidence hash differs from the guest output.'
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead((Join-Path $output 'evidence.zip'))
$extracted = Join-Path $output 'extracted'
try {
    foreach ($entry in $archive.Entries) {
        $resolved = [IO.Path]::GetFullPath((Join-Path $extracted $entry.FullName))
        if (-not $resolved.StartsWith($extracted + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Unexpected path in evidence archive.'
        }
    }
} finally {
    $archive.Dispose()
}
[IO.Compression.ZipFile]::ExtractToDirectory((Join-Path $output 'evidence.zip'), $extracted)
$receipt = [ordered]@{
    id = $job.id
    mode = $Mode
    guest_exit = $record.Result.GuestExitCode
    submit_exit = $testExit
    source_sha256 = $job.source_sha256
    runner_sha256 = $job.runner_sha256
    evidence_sha256 = $evidenceHash
    output = $output
    extracted = $extracted
}
$receipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'RECEIPT.json') -Encoding utf8NoBOM
$receipt | ConvertTo-Json
exit $testExit
