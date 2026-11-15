# Runs inside the guest. Regressions for the Astra review fixes plus the auth mail suite.
$ErrorActionPreference = 'Continue'
if (-not (Test-Path 'J:\')) { $null = & subst.exe J: C:\j }
$root = 'J:\class-assistant\worktrees\web-pr03b-sessions'
Set-Location -LiteralPath $root
$python = "$root\work\pr03b\product-completion-20261110-01\venv\Scripts\python.exe"
"guest=$env:COMPUTERNAME user=$env:USERNAME started_utc=$([DateTime]::UtcNow.ToString('o'))"
& $python -I -B tools\pr03b\product\test_backend.py review 2>&1 | ForEach-Object { "$_" }
$code = $LASTEXITCODE
"backend_review_exit=$code ended_utc=$([DateTime]::UtcNow.ToString('o'))"
exit $code
