# Fixed child entry. Always invoke as a file; stdout is one sanitized receipt.
& {
    Set-StrictMode -Version Latest
    $ErrorActionPreference='Stop'
    $Utf8=New-Object Text.UTF8Encoding($false)
    [Console]::OutputEncoding=$Utf8
    $Home4='C:\Users\Administrator\PR03bBatch4Attempt08'
    $Kit='C:\Users\Administrator\PR03bLabBasic'
    $Python=Join-Path $Kit 'python\python.exe'
    $stage='entry';$started=$false;$exitCode=1
    $receipt=[ordered]@{schema_version=1;kind='batch4_entry_result';status='STOP';database_execution_allowed=$false;
        runner_invoked=$false;target_active=$null;evidence_ready=$false;evidence_zip=$null;evidence_sha256=$null;errors=@()}
    function Get-Hash([string]$Path) {(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
    function Assert-TaskPath([string]$Path,[string]$Root) {
        $full=[IO.Path]::GetFullPath($Path);$prefix=[IO.Path]::GetFullPath($Root).TrimEnd('\')+'\'
        if($full -cne $prefix.TrimEnd('\') -and -not $full.StartsWith($prefix,[StringComparison]::Ordinal)) {throw 'entry_path_outside_root'}
        $p=$full
        while($p) {
            if(Test-Path -LiteralPath $p) {
                if(((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {throw 'entry_reparse_refused'}
            }
            $p=[IO.Path]::GetDirectoryName($p)
        }
    }
    function Write-NewJson([string]$Path,$Value) {
        Assert-TaskPath $Path $Home4
        $data=$Utf8.GetBytes(($Value|ConvertTo-Json -Depth 16)+"`n")
        $f=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try {$f.Write($data,0,$data.Length)} finally {$f.Dispose()}
    }
    function Get-SafeFailure($Record,[string]$Stage) {
        $code=$Record.Exception.Message
        if($code -notmatch '^[A-Za-z0-9_.:-]{1,180}$') {$code='sanitized_exception'}
        $id=$Record.FullyQualifiedErrorId
        if($id -notmatch '^[A-Za-z0-9_.,:-]{1,180}$') {$id='sanitized_error_id'}
        return [ordered]@{stage=$Stage;code=$code;error_type=$Record.Exception.GetType().FullName;
            error_id=$id;script_line=$Record.InvocationInfo.ScriptLineNumber}
    }
    function Invoke-FixedPython([string]$Mode) {
        if($Mode -notin @('selftest','runner')) {throw 'fixed_python_mode_required'}
        $stdout=Join-Path $Home4 ($Mode+'.stdout.txt');$stderr=Join-Path $Home4 ($Mode+'.stderr.txt')
        foreach($file in @($stdout,$stderr)) {Assert-TaskPath $file $Home4;if(Test-Path -LiteralPath $file){throw 'child_log_already_exists'}}
        $code=$null;$summary=$null
        try {
            if($Mode -eq 'selftest') {& $Python -X utf8 -I -S -B (Join-Path $Home4 'selftest.py') 1> $stdout 2> $stderr}
            else {& $Python -X utf8 -I -S -B (Join-Path $Home4 'runner.py') fixed-run 1> $stdout 2> $stderr}
            $code=$LASTEXITCODE
            if((Get-Item -LiteralPath $stdout).Length -gt 1048576) {throw 'child_summary_size_budget'}
            $value=(Get-Content -LiteralPath $stdout -Raw)|ConvertFrom-Json -ErrorAction Stop
            $summary=[ordered]@{}
            foreach($key in @('status','passed','tests','failures','errors','skipped','run_id','target_verified','target_active','database_execution_allowed','checks_total','phase','error_type','code')) {
                if($value.PSObject.Properties.Name -contains $key) {$summary[$key]=$value.$key}
            }
        } finally {
            $meta=[ordered]@{mode=$Mode;exit_code=$code;summary=$summary;logs=[ordered]@{}}
            foreach($p in @($stdout,$stderr)) {if(Test-Path -LiteralPath $p -PathType Leaf){$meta.logs[[IO.Path]::GetFileName($p)]=[ordered]@{bytes=(Get-Item -LiteralPath $p).Length;sha256=(Get-Hash $p)}}}
            Write-NewJson (Join-Path $Home4 ($Mode+'-receipt.json')) $meta
        }
        if($null -eq $code -or $code -ne 0) {throw ('fixed_'+$Mode+'_exit_nonzero')}
        return $summary
    }
    function New-Batch4Evidence([string]$Root) {
        Add-Type -AssemblyName System.IO.Compression
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $files=@('STARTED.json','BUNDLE-MANIFEST.json','approved-inputs.json','prior-attempt04.json','resume-inputs.json','ENTRY-OPERATION.json')
        foreach($name in @('STOP.json','RUN.json','SELFTEST.json','selftest-receipt.json','runner-receipt.json','evidence\results.json')) {
            if(Test-Path -LiteralPath (Join-Path $Root $name) -PathType Leaf) {$files+=$name}
        }
        $final=Join-Path $Root 'BATCH4-EVIDENCE.zip';$partial=Join-Path $Root 'BATCH4-EVIDENCE.partial'
        foreach($p in @($final,$partial)) {Assert-TaskPath $p $Root;if(Test-Path -LiteralPath $p){throw 'evidence_already_exists'}}
        $index=[ordered]@{};$archive=$null;$stream=$null
        try {
            $stream=[IO.File]::Open($partial,[IO.FileMode]::CreateNew,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
            $archive=New-Object IO.Compression.ZipArchive($stream,[IO.Compression.ZipArchiveMode]::Create,$true)
            foreach($name in $files) {
                $p=Join-Path $Root $name;Assert-TaskPath $p $Root
                $info=Get-Item -LiteralPath $p -Force
                if($info.PSIsContainer -or $info.Length -gt 16777216) {throw 'evidence_member_budget'}
                $index[$name.Replace('\','/')]=[ordered]@{bytes=$info.Length;sha256=(Get-Hash $p)}
                $null=[IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,$p,$name.Replace('\','/'),[IO.Compression.CompressionLevel]::Optimal)
            }
            $entry=$archive.CreateEntry('EXPORT-INDEX.json');$entryStream=$entry.Open()
            try {$data=$Utf8.GetBytes(($index|ConvertTo-Json -Depth 8)+"`n");$entryStream.Write($data,0,$data.Length)} finally {$entryStream.Dispose()}
        } finally {
            if($null -ne $archive){$archive.Dispose()}
            if($null -ne $stream){$stream.Dispose()}
        }
        $verify=[IO.Compression.ZipFile]::OpenRead($partial)
        try {
            $wanted=@($index.Keys)+@('EXPORT-INDEX.json')
            if($verify.Entries.Count -ne $wanted.Count -or ((@($verify.Entries|ForEach-Object {$_.FullName}|Sort-Object)-join ',') -cne (@($wanted|Sort-Object)-join ','))) {throw 'evidence_archive_set_differs'}
            foreach($entry in $verify.Entries) {
                if($entry.FullName -eq 'EXPORT-INDEX.json'){continue}
                $reader=$entry.Open();$h=[Security.Cryptography.SHA256]::Create()
                try {$digest=([BitConverter]::ToString($h.ComputeHash($reader))).Replace('-','').ToLowerInvariant()} finally {$h.Dispose();$reader.Dispose()}
                if($entry.Length -ne $index[$entry.FullName].bytes -or $digest -cne $index[$entry.FullName].sha256) {throw 'evidence_archive_bytes_differ'}
            }
        } finally {$verify.Dispose()}
        Assert-TaskPath $partial $Root;Assert-TaskPath $final $Root
        [IO.File]::Move($partial,$final)
        return [ordered]@{path=$final;sha256=(Get-Hash $final);bytes=(Get-Item -LiteralPath $final).Length;verified=$true}
    }
    try {
        if($args.Count -ne 0 -or $PSScriptRoot -cne $Home4 -or $PSVersionTable.PSEdition -ne 'Desktop' -or
            $PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1 -or -not [Environment]::Is64BitProcess) {throw 'fixed_guest_path_and_PS51_required'}
        $stage='platform'
        $p=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
        if(-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'guest_admin_coordinator_required'}
        $stage='one_shot_guard';Assert-TaskPath $Home4 $Home4
        foreach($name in @('STARTED.json','STOP.json','RUN.json','SELFTEST.json','ENTRY-RESULT.json','BATCH4-EVIDENCE.zip','BATCH4-EVIDENCE.partial','evidence','selftest.stdout.txt','runner.stdout.txt')) {
            if(Test-Path -LiteralPath (Join-Path $Home4 $name)) {throw 'one_shot_already_used_preserve'}
        }
        $stage='runtime_pins'
        foreach($item in @(
            @('PAYLOAD-MANIFEST.json','4c34bea1b0dcb4f5f10c158e50461da397fc369e8f3825f5f34f5e4309f43074'),
            @('python\python.exe','3c48fbdcc2ed5c55290e39bef50bd6b1da76f43f3069a4a2624d0b8876a605f5'),
            @('python\python312.dll','69b91d62f73a21a0aac361c1ffe2e970bf5762756073e8e2acd36e6e2f516133'))) {
            $path=Join-Path $Kit $item[0];Assert-TaskPath $path $Kit
            if((Get-Hash $path) -cne $item[1]) {throw 'private_runtime_pin_mismatch'}
        }
        $stage='bundle_pins'
        $manifest=Get-Content -LiteralPath (Join-Path $Home4 'BUNDLE-MANIFEST.json') -Raw|ConvertFrom-Json
        $wanted=@('common.py','schema.py','cases.py','runner.py','restricted_process.py','selftest.py','coordinator.ps1','START-IN-GUEST.ps1','read_only_guard.ps1','approved-inputs.json','prior-attempt04.json','resume-inputs.json','README.txt')|Sort-Object
        if((@($manifest.files.PSObject.Properties.Name|Sort-Object)-join ',') -cne ($wanted-join ',')) {throw 'bundle_member_set_differs'}
        foreach($entry in $manifest.files.PSObject.Properties) {
            $path=Join-Path $Home4 $entry.Name;Assert-TaskPath $path $Home4
            if($entry.Value -notmatch '^[0-9a-f]{64}$' -or (Get-Hash $path) -cne $entry.Value) {throw 'bundle_hash_mismatch'}
        }
        $stage='start_marker'
        Write-NewJson (Join-Path $Home4 'STARTED.json') ([ordered]@{time_utc=[DateTime]::UtcNow.ToString('o');pid=$PID;scope='Batch4 fixed SQL only';database_execution_allowed=$false})
        $started=$true
        $stage='selftest';$selftest=Invoke-FixedPython 'selftest'
        if($selftest.passed -ne $true -or $selftest.tests -ne 24 -or $selftest.failures -ne 0 -or $selftest.errors -ne 0 -or $selftest.skipped -ne 0) {throw 'selftest_summary_refused'}
        $stage='runner';$receipt.runner_invoked=$true;$summary=Invoke-FixedPython 'runner'
        $stage='real_result'
        $result=Get-Content -LiteralPath (Join-Path $Home4 'evidence\results.json') -Raw|ConvertFrom-Json
        if($summary.status -cne 'PASS' -or $result.status -cne 'PASS' -or $result.passed -ne $true -or $result.target_verified -ne $true -or
            $result.target_active -isnot [bool] -or $result.target_active -ne $false -or $result.database_execution_allowed -ne $false -or
            $result.checks_total -ne 1254 -or $result.checks_passed -ne 1254 -or @($result.phases).Count -ne 19 -or @($result.guard_snapshots).Count -ne 42 -or
            $result.job_cleanup_confirmed -ne $true -or $result.listener_removed -ne $true -or $result.sources_and_old_inputs_unchanged -ne $true -or
            $result.fixed_script_lease_revoked -ne $true -or $result.business_migrations_executed -ne $false -or $result.business_data_modified -ne $false) {throw 'real_result_incomplete_or_failed'}
        $receipt.target_active=$false;$receipt.status='RUNNER_PASS_EXPORT_PENDING'
    } catch {
        $receipt.errors+=Get-SafeFailure $_ $stage
        if($started) {Write-NewJson (Join-Path $Home4 'STOP.json') ([ordered]@{status='STOP';stage=$stage;failure=$receipt.errors[-1];time_utc=[DateTime]::UtcNow.ToString('o');database_execution_allowed=$false})}
    }
    if($started) {
        try {
            Write-NewJson (Join-Path $Home4 'ENTRY-OPERATION.json') $receipt
            $stage='evidence_export';$export=New-Batch4Evidence $Home4
            $receipt.evidence_ready=$true;$receipt.evidence_zip=$export.path;$receipt.evidence_sha256=$export.sha256
            if($receipt.status -ceq 'RUNNER_PASS_EXPORT_PENDING') {$receipt.status='PASS';$exitCode=0}
        } catch {$receipt.status='STOP';$receipt.errors+=Get-SafeFailure $_ $stage}
        $receipt['stage']=$stage
        try {Write-NewJson (Join-Path $Home4 'ENTRY-RESULT.json') $receipt}
        catch {$receipt.status='STOP';$receipt.errors+=Get-SafeFailure $_ 'receipt_write';$exitCode=1}
    }
    $receipt['stage']=$stage
    [Console]::WriteLine(($receipt|ConvertTo-Json -Depth 12 -Compress))
    exit $exitCode
}
