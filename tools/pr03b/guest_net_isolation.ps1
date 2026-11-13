[CmdletBinding()]
param(
    [ValidateSet('Apply', 'Rollback')][string]$Mode = 'Apply',
    [string]$Case = ''
)

# Dedicated guest only. No host/hypervisor operations, firewall edits or PG server.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Kit = 'C:\Users\Administrator\PR03bLabBasic'
$HomeDir = 'C:\Users\Administrator\PR03bNetworkIsolation'
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Assert-Ordinary([string]$Path) {
    $p = [IO.Path]::GetFullPath($Path)
    if ($p.StartsWith('\\') -or [IO.Path]::GetPathRoot($p) -ne 'C:\') { throw 'Guest-local C path required' }
    while ($p) {
        if (Test-Path -LiteralPath $p) {
            $i = Get-Item -LiteralPath $p -Force
            if (($i.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse path refused' }
        }
        $p = [IO.Path]::GetDirectoryName($p)
    }
}

function Write-NewJson([string]$Path, $Value) {
    Assert-Ordinary $Path
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $bytes = $Utf8.GetBytes(($Value | ConvertTo-Json -Depth 12) + "`n")
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
}

function Get-Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

function Assert-Guest {
    if ($PSVersionTable.PSEdition -ne 'Desktop' -or $PSVersionTable.PSVersion.Major -ne 5 -or
        $PSVersionTable.PSVersion.Minor -ne 1 -or -not [Environment]::Is64BitProcess) { throw '64-bit Windows PowerShell 5.1 required' }
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run in guest elevated local console' }
    $sender = Get-Variable PSSenderInfo -ErrorAction SilentlyContinue
    if ($sender -and $null -ne $sender.Value) { throw 'Remote session refused' }
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    if ($cs.Manufacturer -notmatch '^VMware' -or $cs.Model -notmatch '^VMware' -or $os.BuildNumber -ne '26100') {
        throw 'Dedicated VMware Windows 26100 guest required; do not run on host'
    }
    if ([IO.Path]::GetFullPath($PSScriptRoot) -ne $HomeDir) { throw 'Wrong supplement directory' }
    Assert-Ordinary $HomeDir
    foreach ($name in @('Get-NetAdapter','Disable-NetAdapter','Enable-NetAdapter','Get-NetIPInterface','Get-NetRoute','Get-NetIPAddress','Get-NetCompartment')) {
        $cmd = Get-Command -Name $name
        if ($cmd.ModuleName -notin @('NetAdapter', 'NetTCPIP')) { throw 'Unexpected network cmdlet provider' }
    }
    $bundle = Get-Content -LiteralPath (Join-Path $HomeDir 'BUNDLE-MANIFEST.json') -Raw | ConvertFrom-Json
    $wantedMembers = @('README.txt','guest_net_isolation.ps1','net_isolation_probe.py') | Sort-Object
    if ($bundle.schema_version -ne 1 -or
        ((@($bundle.files.PSObject.Properties.Name | Sort-Object) -join ',') -ne ($wantedMembers -join ','))) { throw 'Supplement member set differs' }
    foreach ($entry in $bundle.files.PSObject.Properties) {
        if ($entry.Name -notmatch '^[A-Za-z0-9_.-]+$') { throw 'Invalid supplement member' }
        $path = Join-Path $HomeDir $entry.Name
        Assert-Ordinary $path
        if ((Get-Hash $path) -ne $entry.Value) { throw 'Supplement hash mismatch' }
    }
    if ($Mode -eq 'Apply') {
        Assert-Ordinary $Kit
        if (-not (Test-Path -LiteralPath (Join-Path $Kit 'BASIC-USER-TOKEN.json'))) { throw 'Approved lab marker absent' }
        if ((Get-Hash (Join-Path $Kit 'PAYLOAD-MANIFEST.json')) -ne '4c34bea1b0dcb4f5f10c158e50461da397fc369e8f3825f5f34f5e4309f43074' -or
            (Get-Hash (Join-Path $Kit 'python\python.exe')) -ne '3c48fbdcc2ed5c55290e39bef50bd6b1da76f43f3069a4a2624d0b8876a605f5' -or
            (Get-Hash (Join-Path $Kit 'python\python312.dll')) -ne '69b91d62f73a21a0aac361c1ffe2e970bf5762756073e8e2acd36e6e2f516133') { throw 'Approved kit pin mismatch' }
    }
}

function Get-MachineBinding {
    $id = (Get-CimInstance -ClassName Win32_ComputerSystemProduct).UUID
    $sha = [Security.Cryptography.SHA256]::Create()
    try { ([BitConverter]::ToString($sha.ComputeHash($Utf8.GetBytes($id)))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-Snapshot {
    $adapters = @(NetAdapter\Get-NetAdapter -IncludeHidden | ForEach-Object {
        [ordered]@{ guid=([guid]$_.InterfaceGuid).ToString(); index=[int]$_.ifIndex; name=$_.Name;
            admin=[int]$_.InterfaceAdminStatus; status=$_.Status.ToString(); type=[int]$_.InterfaceType }
    })
    $dotnet = @([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | ForEach-Object {
        $v4 = $null; $v6 = $null
        if ($_.Supports([Net.NetworkInformation.NetworkInterfaceComponent]::IPv4)) { $v4=$_.GetIPProperties().GetIPv4Properties().Index }
        if ($_.Supports([Net.NetworkInformation.NetworkInterfaceComponent]::IPv6)) { $v6=$_.GetIPProperties().GetIPv6Properties().Index }
        [ordered]@{ id=$_.Id; type=[int]$_.NetworkInterfaceType; operational=[int]$_.OperationalStatus; index4=$v4; index6=$v6 }
    })
    [ordered]@{
        time_utc=[DateTime]::UtcNow.ToString('o'); machine_binding=(Get-MachineBinding)
        powershell=$PSVersionTable.PSVersion.ToString(); adapters=$adapters; native_interfaces=$dotnet
        compartments=@(NetTCPIP\Get-NetCompartment | ForEach-Object { [ordered]@{id=[int]$_.CompartmentId; description=$_.CompartmentDescription} })
        interfaces=@(NetTCPIP\Get-NetIPInterface -IncludeAllCompartments | ForEach-Object {
            [ordered]@{index=[int]$_.InterfaceIndex; family=$_.AddressFamily.ToString(); connected=[int]$_.ConnectionState} })
        addresses=@(NetTCPIP\Get-NetIPAddress -IncludeAllCompartments | ForEach-Object {
            [ordered]@{index=[int]$_.InterfaceIndex; address=$_.IPAddress; family=$_.AddressFamily.ToString()} })
        routes=@(NetTCPIP\Get-NetRoute -PolicyStore ActiveStore -IncludeAllCompartments | ForEach-Object {
            [ordered]@{index=[int]$_.InterfaceIndex; destination=$_.DestinationPrefix; next_hop=$_.NextHop} })
    }
}

function Get-LoopIndex($Snapshot) {
    $loops = @($Snapshot.native_interfaces | Where-Object { $_.type -eq 24 })
    if ($loops.Count -ne 1 -or $loops[0].operational -ne 1 -or
        $null -eq $loops[0].index4 -or $null -eq $loops[0].index6 -or
        $loops[0].index4 -ne $loops[0].index6) { throw 'One live dual-stack software-loopback required' }
    $idx = [int]$loops[0].index4
    foreach ($ip in @('127.0.0.1', '::1')) {
        if (@($Snapshot.addresses | Where-Object { $_.index -eq $idx -and $_.address -eq $ip }).Count -ne 1) { throw 'Loopback address mapping differs' }
    }
    return $idx
}

function Assert-Scope($Snapshot) {
    if (@($Snapshot.compartments).Count -ne 1 -or $Snapshot.compartments[0].id -ne 1) {
        throw 'Extra network compartment unsupported; preserve evidence, do not modify it'
    }
    $idx = Get-LoopIndex $Snapshot
    $guids = @($Snapshot.adapters | ForEach-Object { $_.guid })
    if ($guids.Count -eq 0 -or @($guids | Select-Object -Unique).Count -ne $guids.Count) { throw 'Adapter identity absent/duplicate' }
    foreach ($a in $Snapshot.adapters) {
        if ($a.index -ne $idx -and ($a.type -eq 24 -or $a.admin -notin @(1,2) -or $a.index -le 0)) { throw 'Unexplained adapter state' }
    }
    return $idx
}

function Assert-IsolationIdentity($Snapshot, $Plan) {
    $idx = Assert-Scope $Snapshot
    if ($Snapshot.machine_binding -ne $Plan.machine_binding -or $idx -ne $Plan.loopback_index) { throw 'Guest/loopback identity changed' }
    $current = @($Snapshot.adapters | ForEach-Object { $_.guid + '|' + $_.index + '|' + $_.type } | Sort-Object)
    $original = @($Plan.before.adapters | ForEach-Object { $_.guid + '|' + $_.index + '|' + $_.type } | Sort-Object)
    if (($current -join ',') -ne ($original -join ',')) { throw 'Adapter set changed' }
    return $idx
}

function Assert-Isolated($Snapshot, $Plan) {
    $idx = Assert-IsolationIdentity $Snapshot $Plan
    foreach ($a in $Snapshot.adapters) { if ($a.index -ne $idx -and $a.admin -ne 2) { throw 'Non-loopback adapter remains enabled' } }
    foreach ($i in $Snapshot.native_interfaces) { if ($i.type -ne 24 -and $i.operational -notin @(2,5,6,7)) { throw 'Non-loopback native interface is live/unknown' } }
    foreach ($i in $Snapshot.interfaces) { if ($i.index -ne $idx -and $i.connected -ne 0) { throw 'Non-loopback IP interface is live/unknown' } }
    foreach ($r in $Snapshot.routes) { if ($r.index -ne $idx -or $r.destination -in @('0.0.0.0/0','::/0')) { throw 'Residual non-loopback route' } }
}

function Assert-Plan($Plan) {
    $idx = Assert-Scope $Plan.before
    if ($Plan.machine_binding -ne $Plan.before.machine_binding -or $Plan.loopback_index -ne $idx) { throw 'Plan/before binding mismatch' }
    $expected = @($Plan.before.adapters | Where-Object { $_.index -ne $idx -and $_.admin -eq 1 } |
        ForEach-Object { $_.guid + '|' + $_.index + '|1' } | Sort-Object)
    $actual = @($Plan.targets | ForEach-Object { $_.guid + '|' + $_.index + '|' + $_.admin } | Sort-Object)
    if ($expected.Count -eq 0 -or $expected.Count -ne $actual.Count -or ($expected -join ',') -ne ($actual -join ',')) {
        throw 'Targets differ from exact original AdminUp set'
    }
}

function Invoke-Probe([string]$Phase, [string]$Test = '') {
    $label = $Phase
    $argsList = @('-I','-S','-B',(Join-Path $HomeDir 'net_isolation_probe.py'),'--phase',$Phase,'--case',$Case)
    if ($Test) { $argsList += @('--test',$Test); $label += '-' + $Test }
    & (Join-Path $Kit 'python\python.exe') @argsList
    if ($LASTEXITCODE -ne 0) { throw ('Native probe STOP: ' + $label) }
    $receipt = Get-Content -LiteralPath (Join-Path $Case ($label + '-probe.json')) -Raw | ConvertFrom-Json
    if ($receipt.passed -ne $true) { throw 'Probe receipt is not PASS' }
}

function Export-Case([string]$Suffix = '') {
    $zip = Join-Path $HomeDir ('evidence-' + (Split-Path $Case -Leaf) + $Suffix + '.zip')
    if (Test-Path -LiteralPath $zip) { throw 'Evidence ZIP exists; no overwrite' }
    $members = @(Get-ChildItem -LiteralPath $Case -File)
    if (@(Get-ChildItem -LiteralPath $Case -Directory).Count -ne 0 -or
        @($members | Where-Object { $_.Extension -notin @('.json','.txt','.empty') }).Count -ne 0) { throw 'Unexpected evidence member' }
    $index = @($members | ForEach-Object { [ordered]@{name=$_.Name; bytes=$_.Length; sha256=(Get-Hash $_.FullName)} })
    Write-NewJson (Join-Path $Case ('EXPORT-INDEX' + $Suffix + '.json')) ([ordered]@{run_id=(Split-Path $Case -Leaf); files=$index; no_pgdata_or_passwords=$true})
    Compress-Archive -LiteralPath @((Get-ChildItem -LiteralPath $Case -File).FullName) -DestinationPath $zip
    Write-Host ('EVIDENCE: ' + $zip)
    Write-Host ('SHA256: ' + (Get-Hash $zip))
}

$exitCode = 1
$caseOwned = $false
try {
    Assert-Guest
    $cases = Join-Path $HomeDir 'cases'
    Assert-Ordinary $cases
    if ($Mode -eq 'Apply') {
        if ($Case) { throw 'Apply never accepts an old case' }
        if (Test-Path -LiteralPath $cases) { throw 'Prior case exists; preserve it and obtain review, do not rerun' }
        New-Item -ItemType Directory -Path $cases | Out-Null
        $Case = Join-Path $cases ('run-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $Case | Out-Null
        $caseOwned = $true
        Write-NewJson (Join-Path $Case 'STARTED.json') ([ordered]@{mode=$Mode; run_id=(Split-Path $Case -Leaf); time_utc=[DateTime]::UtcNow.ToString('o'); database_execution_allowed=$false})
        if (@(Get-Process -Name postgres,postmaster -ErrorAction SilentlyContinue).Count -ne 0) { throw 'Existing PostgreSQL process found; do not operate it' }
        Invoke-Probe 'preflight'
        $before = Get-Snapshot
        Write-NewJson (Join-Path $Case 'before.json') $before
        $loop = Assert-Scope $before
        $targets = @($before.adapters | Where-Object { $_.index -ne $loop -and $_.admin -eq 1 })
        if ($targets.Count -eq 0) { throw 'No enabled adapter for a causal before/after control' }
        foreach ($i in $before.native_interfaces) {
            if ($i.type -ne 24 -and $i.operational -eq 1 -and
                @($targets | Where-Object { $_.index -eq $i.index4 -or $_.index -eq $i.index6 }).Count -eq 0) { throw 'Unmapped live interface' }
        }
        Invoke-Probe 'baseline'
        $plan = [ordered]@{run_id=(Split-Path $Case -Leaf); machine_binding=$before.machine_binding; loopback_index=$loop;
            before=$before; targets=$targets; intent='Disable guest adapters; keep isolated pending review'; general_database_execution_allowed=$false}
        Assert-Plan $plan
        Write-NewJson (Join-Path $Case 'PLAN.json') $plan
        Write-NewJson (Join-Path $Case 'RECOVERY.json') ([ordered]@{
            command=('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $HomeDir 'guest_net_isolation.ps1') + '" -Mode Rollback -Case "' + $Case + '"');
            effect='Revoke validation, restore only this run recorded/attempted original-Up GUIDs; never automatic'})
        foreach ($a in $targets) {
            $live = @(NetAdapter\Get-NetAdapter -IncludeHidden | Where-Object { ([guid]$_.InterfaceGuid).ToString() -eq $a.guid })
            if ($live.Count -ne 1 -or [int]$live[0].InterfaceAdminStatus -ne 1 -or [int]$live[0].ifIndex -ne $a.index) { throw 'Adapter changed before disable' }
            Write-NewJson (Join-Path $Case ('disable-intent-' + $a.guid + '.json')) $a
            NetAdapter\Disable-NetAdapter -InputObject $live[0] -Confirm:$false
            Write-NewJson (Join-Path $Case ('disable-returned-' + $a.guid + '.json')) ([ordered]@{guid=$a.guid; time_utc=[DateTime]::UtcNow.ToString('o')})
        }
        # Only observe expected OS convergence; never repeat a mutator or probe.
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        $settled = $false
        $attempt = 0
        do {
            $attempt++
            $snap = Get-Snapshot
            Write-NewJson (Join-Path $Case ('settling-' + $attempt.ToString('D3') + '.json')) $snap
            $null = Assert-IsolationIdentity $snap $plan
            try { Assert-Isolated $snap $plan; $settled=$true }
            catch { if ([DateTime]::UtcNow -ge $deadline) { throw 'OS isolation did not converge; preserve all observations' } }
            if (-not $settled) { Start-Sleep -Milliseconds 250 }
        } while (-not $settled)
        foreach ($test in @('tcp4','tcp6','udp4','udp6','psql4','psql6','loop4','loop6')) {
            $snap = Get-Snapshot
            Write-NewJson (Join-Path $Case ($test + '-before.json')) $snap
            Assert-Isolated $snap $plan
            Invoke-Probe 'isolated' $test
            $snap = Get-Snapshot
            Write-NewJson (Join-Path $Case ($test + '-after.json')) $snap
            Assert-Isolated $snap $plan
        }
        Invoke-Probe 'postflight'
        $last = Get-Snapshot
        Write-NewJson (Join-Path $Case 'after.json') $last
        Assert-Isolated $last $plan
        Write-NewJson (Join-Path $Case 'RESULT.json') ([ordered]@{
            passed=$true; run_id=$plan.run_id; time_utc=[DateTime]::UtcNow.ToString('o');
            os_network_isolation_observed=$true; observation_scope='This guest, this validation interval; fresh checks required for future work';
            database_execution_allowed=$false; postgres_started=$false; SQL_executed=$false; retained_isolated=$true;
            shared_folders_clipboard_and_VMCI_isolation_proven=$false; mechanism_experiments_authorized=$false})
        $exitCode = 0
    } else {
        $Case = [IO.Path]::GetFullPath($Case)
        if ((Split-Path $Case -Parent) -ne $cases -or (Split-Path $Case -Leaf) -notmatch '^run-[0-9a-f]{32}$') { throw 'Exact original case path required' }
        Assert-Ordinary $Case
        $plan = Get-Content -LiteralPath (Join-Path $Case 'PLAN.json') -Raw | ConvertFrom-Json
        if ($plan.run_id -ne (Split-Path $Case -Leaf) -or $plan.machine_binding -ne (Get-MachineBinding)) { throw 'Rollback guest/case binding differs' }
        $caseOwned = $true
        Write-NewJson (Join-Path $Case 'REVOKED.json') ([ordered]@{time_utc=[DateTime]::UtcNow.ToString('o'); validation_current=$false; reason='Explicit rollback requested before enable'})
        Assert-Plan $plan
        foreach ($a in $plan.targets) {
            $intent = Join-Path $Case ('disable-intent-' + $a.guid + '.json')
            if (-not (Test-Path -LiteralPath $intent)) { continue }
            $record = Get-Content -LiteralPath $intent -Raw | ConvertFrom-Json
            if ($record.guid -ne $a.guid -or $record.admin -ne 1 -or $record.index -ne $a.index) { throw 'Rollback intent mismatch' }
            $live = @(NetAdapter\Get-NetAdapter -IncludeHidden | Where-Object { ([guid]$_.InterfaceGuid).ToString() -eq $a.guid })
            if ($live.Count -ne 1 -or [int]$live[0].InterfaceAdminStatus -ne 2 -or [int]$live[0].ifIndex -ne $a.index) { throw 'Rollback adapter missing/changed; no broad enable' }
            NetAdapter\Enable-NetAdapter -InputObject $live[0] -Confirm:$false
            $check = @(NetAdapter\Get-NetAdapter -IncludeHidden | Where-Object { ([guid]$_.InterfaceGuid).ToString() -eq $a.guid })
            if ($check.Count -ne 1 -or [int]$check[0].InterfaceAdminStatus -ne 1) { throw 'Enable not confirmed' }
            Write-NewJson (Join-Path $Case ('enabled-' + $a.guid + '.json')) ([ordered]@{guid=$a.guid; restored_original_admin_up=$true})
        }
        Write-NewJson (Join-Path $Case 'ROLLBACK.json') ([ordered]@{passed=$true; validation_current=$false; database_execution_allowed=$false; after=(Get-Snapshot)})
        Write-Host 'ROLLBACK complete: original attempted adapter states restored; isolation validation revoked.'
        $exitCode = 0
    }
} catch {
    # No automatic enable/restore or repeated attempt. Partial disable remains visible.
    if ($caseOwned -and (Test-Path -LiteralPath $Case)) {
        Write-NewJson (Join-Path $Case ('STOP-' + [guid]::NewGuid().ToString('N') + '.json')) ([ordered]@{
            passed=$false; mode=$Mode; time_utc=[DateTime]::UtcNow.ToString('o'); error_type=$_.Exception.GetType().FullName;
            reason=$_.Exception.Message; automatic_rollback=$false; database_execution_allowed=$false})
    }
    Write-Host ('STOP: ' + $_.Exception.Message) -ForegroundColor Red
} finally {
    if ($caseOwned -and (Test-Path -LiteralPath $Case)) {
        Write-Host ('CASE: ' + $Case)
        try { if ($Mode -eq 'Apply') { Export-Case } else { Export-Case '-rollback' } }
        catch { $exitCode=1; Write-Host ('STOP evidence export: ' + $_.Exception.GetType().FullName) -ForegroundColor Red }
        if ($exitCode -eq 0 -and $Mode -eq 'Apply') {
            Write-Host 'PASS: guest IP isolation observed; adapters stay disabled; no database execution authorization.' -ForegroundColor Green
        }
    }
}
exit $exitCode
