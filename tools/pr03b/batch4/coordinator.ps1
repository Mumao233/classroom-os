# Fixed, read-only snapshot child. No adapter/firewall/service modification.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$Utf8=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=$Utf8
$Home4='C:\Users\Administrator\PR03bBatch4Attempt08'
$Old='C:\Users\Administrator\PR03bNetworkContinuation03'
$OriginalPlan='C:\Users\Administrator\PR03bNetworkIsolation\cases\run-7ecce1d9173448418087a4167a4413ce\PLAN.json'
$stage='platform'
function Assert-LocalPath([string]$Path) {
    $p=[IO.Path]::GetFullPath($Path)
    if($p.StartsWith('\\') -or [IO.Path]::GetPathRoot($p) -ne 'C:\') {throw 'local_path_required'}
    while($p) {
        if(Test-Path -LiteralPath $p) {
            if(((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {throw 'reparse_path_refused'}
        }
        $p=[IO.Path]::GetDirectoryName($p)
    }
}
function Get-Hash([string]$Path) {(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function Get-SystemPowerShellIdentity {
    $path='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    Assert-LocalPath $path
    $item=Get-Item -LiteralPath $path -Force
    if($item.PSIsContainer -or $item.Length -le 0) {throw 'windows_image_regular_file_required'}
    $before=Get-Hash $path
    $signature=Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $path -ErrorAction Stop
    if($signature.Status.ToString() -ne 'Valid' -or $signature.IsOSBinary -ne $true -or
        $null -eq $signature.SignerCertificate -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)' -or
        $signature.SignatureType.ToString() -notin @('Catalog','Authenticode')) {throw 'valid_Microsoft_Windows_OS_signature_required'}
    if((Get-Hash $path) -ne $before -or (Get-Item -LiteralPath $path -Force).Length -ne $item.Length) {throw 'windows_image_changed'}
    return [ordered]@{schema_version=1;path=$path;bytes=$item.Length;sha256=$before;signature_status='Valid';is_os_binary=$true;
        signature_type=$signature.SignatureType.ToString();signer_subject=$signature.SignerCertificate.Subject;
        signer_thumbprint=$signature.SignerCertificate.Thumbprint;time_utc=[DateTime]::UtcNow.ToString('o')}
}
function Assert-MaintenanceServices($services,$reference) {
    if($services.Count -ne 4 -or @($reference.services).Count -ne 4){throw 'maintenance_service_set_changed'}
    foreach($s in $reference.services){
        $now=@($services|Where-Object {$_.name -ceq $s.name})
        if($now.Count -ne 1 -or $now[0].state -cne 'Stopped' -or @($now[0].active_dependents).Count -ne 0){throw 'maintenance_service_not_stopped'}
        foreach($key in @('start_mode','path','account','service_type')){if($now[0].$key -cne $s.$key){throw 'maintenance_service_config_changed'}}
        if(($now[0].dependencies -join ',') -cne ($s.dependencies -join ',')){throw 'maintenance_SCM_dependencies_changed'}
    }
}
function Assert-MaintenanceRegistry($registry,$reference) {
    if(($registry|ConvertTo-Json -Depth 8 -Compress) -cne ($reference.registry_dependencies|ConvertTo-Json -Depth 8 -Compress)){throw 'maintenance_registry_dependencies_changed'}
}
function Get-MaintenanceState {
    $path=Join-Path $Home4 'resume-inputs.json';Assert-LocalPath $path
    if((Get-Hash $path) -cne '43743f0e5fecdb89cfd930bc02c8ed2283d8798155fa7ab9b2e7c9cc5fc10f43'){throw 'resume_manifest_changed'}
    $reference=Get-Content -LiteralPath $path -Raw -Encoding UTF8|ConvertFrom-Json
    $services=@(Get-CimInstance Win32_Service -Filter "Name='RasMan' OR Name='SstpSvc' OR Name='RasAuto' OR Name='RemoteAccess'" -OperationTimeoutSec 10|Sort-Object Name|ForEach-Object {
        $c=Microsoft.PowerShell.Management\Get-Service -Name $_.Name -ErrorAction Stop;$c.Refresh()
        [ordered]@{name=$_.Name;state=$c.Status.ToString();start_mode=$_.StartMode;path=$_.PathName;account=$_.StartName;
            service_type=$_.ServiceType;dependencies=@($c.ServicesDependedOn|ForEach-Object {$_.ServiceName}|Sort-Object);
            active_dependents=@($c.DependentServices|Where-Object {$_.Status -ne 'Stopped'}|ForEach-Object {$_.ServiceName}|Sort-Object)}
    })
    Assert-MaintenanceServices $services $reference
    $registry=[ordered]@{}
    foreach($name in @('RasAuto','RasMan','RemoteAccess','SstpSvc')){
        $key=[Microsoft.Win32.Registry]::LocalMachine.OpenSubKey(('SYSTEM\CurrentControlSet\Services\'+$name),$false)
        if($null -eq $key){throw 'maintenance_registry_key_missing'}
        try{
            $names=@($key.GetValueNames());$row=[ordered]@{}
            foreach($field in @('DependOnService','DependOnGroup')){
                if($field -in $names){
                    $value=$key.GetValue($field,$null,[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                    if($value -isnot [string] -and $value -isnot [string[]]){throw 'maintenance_registry_dependency_type'}
                    $row[$field]=[ordered]@{present=$true;kind=$key.GetValueKind($field).ToString();value=@($value)}
                }else{$row[$field]=[ordered]@{present=$false;kind=$null;value=@()}}
            }
            $registry[$name]=$row
        }finally{$key.Dispose()}
    }
    Assert-MaintenanceRegistry $registry $reference
    return [ordered]@{services_stopped=$true;registry_matches=$true;resume_pin='43743f0e5fecdb89cfd930bc02c8ed2283d8798155fa7ab9b2e7c9cc5fc10f43';
        services=$services;registry_dependencies=$registry}
}
try {
    if($args.Count -ne 0 -or $PSScriptRoot -cne $Home4 -or $PSVersionTable.PSEdition -ne 'Desktop' -or
        $PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1 -or -not [Environment]::Is64BitProcess) {throw 'fixed_PS51_guest_required'}
    $principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'read_only_OS_observer_admin_required'}
    $sender=Get-Variable PSSenderInfo -ErrorAction SilentlyContinue
    if($sender -and $null -ne $sender.Value) {throw 'remote_session_refused'}
    $cs=Get-CimInstance Win32_ComputerSystem;$os=Get-CimInstance Win32_OperatingSystem
    if($cs.Manufacturer -notmatch '^VMware' -or $cs.Model -notmatch '^VMware' -or $os.BuildNumber -ne '26100') {throw 'dedicated_guest_required'}
    $stage='source_pins';foreach($p in @($Home4,$Old,$OriginalPlan)) {Assert-LocalPath $p}
    if((Get-Hash (Join-Path $Home4 'read_only_guard.ps1')) -ne '2a5535619651ac4835374c0777edff26f5759f9038de27c3edd4fcfc3d998a1c') {throw 'guard_bytes_changed'}
    if((Get-Hash $OriginalPlan) -ne 'a7b74277cf46156c43062e1d540a796162a898616d0695bb510cdfac28e4fa38') {throw 'original_plan_changed'}
    $recordPath=Join-Path $Old 'SYSTEM-POWERSHELL.json'
    if((Get-Hash $recordPath) -ne 'a6384460be775cbbe65077572486de964c81e98613d8fe8424a9c8720b7804c6') {throw 'system_record_changed'}
    $stage='system_identity';$record=Get-Content -LiteralPath $recordPath -Raw|ConvertFrom-Json
    $system=Get-SystemPowerShellIdentity
    foreach($key in @('schema_version','path','bytes','sha256','signature_status','is_os_binary','signature_type','signer_subject','signer_thumbprint')) {
        if($record.$key -cne $system[$key]) {throw 'windows_image_identity_changed'}
    }
    $stage='original_isolation';. (Join-Path $Home4 'read_only_guard.ps1')
    $plan=Get-Content -LiteralPath $OriginalPlan -Raw|ConvertFrom-Json
    Assert-Plan $plan
    $snapshot=Get-Snapshot
    Assert-Isolated $snapshot $plan
    $stage='maintenance_state';$maintenance=Get-MaintenanceState
    $stage='pg_observation'
    $pids=@(Get-Process -Name postgres,postmaster -ErrorAction SilentlyContinue|ForEach-Object {[int]$_.Id})
    [ordered]@{passed=$true;snapshot=$snapshot;pg_pids=$pids;system=$system;maintenance=$maintenance}|ConvertTo-Json -Depth 16 -Compress
    exit 0
} catch {
    $code=$_.Exception.Message
    $guardErrors=@{'Adapter set changed'='adapter_set_changed';'Non-loopback adapter remains enabled'='non_loopback_adapter_enabled';
        'Non-loopback native interface is live/unknown'='non_loopback_native_live';'Non-loopback IP interface is live/unknown'='non_loopback_IP_live';
        'Residual non-loopback route'='non_loopback_route'}
    if($guardErrors.ContainsKey($code)){$code=$guardErrors[$code]}
    if($code -notmatch '^[A-Za-z0-9_.:-]{1,180}$'){$code='sanitized_snapshot_failure'}
    [ordered]@{passed=$false;stage=$stage;failure=[ordered]@{code=$code;error_type=$_.Exception.GetType().FullName};database_execution_allowed=$false}|ConvertTo-Json -Depth 8 -Compress
    exit 1
}
