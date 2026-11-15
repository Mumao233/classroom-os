# Runs inside the guest. Frontend contract generation check, typecheck, lint, Vitest,
# production build and build verification.
$ErrorActionPreference = 'Continue'
if (-not (Test-Path 'J:\')) { $null = & subst.exe J: C:\j }
$root = 'J:\class-assistant\worktrees\web-pr03b-sessions'
Set-Location -LiteralPath $root
$env:PATH = "$root\work\pr03b\vmware-broker\payload\node;$env:PATH"
$python = "$root\work\pr03b\product-completion-20261110-01\venv\Scripts\python.exe"
"guest=$env:COMPUTERNAME user=$env:USERNAME node=$(node --version) started_utc=$([DateTime]::UtcNow.ToString('o'))"
& $python -I -B tools\pr03b\product\run_frontend.py checks 2>&1 | ForEach-Object { "$_" }
$code = $LASTEXITCODE
"--- vitest summary"
$log = Get-ChildItem -LiteralPath "$root\outputs\pr03b\product-completion-20261110-01" -Filter 'frontend-checks-*-3.log' | Sort-Object LastWriteTime | Select-Object -Last 1
if ($log) { Get-Content -LiteralPath $log.FullName -Encoding UTF8 -Tail 12 }
"frontend_checks_exit=$code ended_utc=$([DateTime]::UtcNow.ToString('o'))"
exit $code
