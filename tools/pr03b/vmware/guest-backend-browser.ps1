# Runs inside the guest. Real Chromium over HTTPS/WSS against the formal app.
# Run guest-frontend-checks.ps1 first: this test serves the frontend build it produces.
$ErrorActionPreference = 'Continue'
if (-not (Test-Path 'J:\')) { $null = & subst.exe J: C:\j }
$root = 'J:\class-assistant\worktrees\web-pr03b-sessions'
Set-Location -LiteralPath $root
$python = "$root\work\pr03b\product-completion-20261110-01\venv\Scripts\python.exe"
"guest=$env:COMPUTERNAME user=$env:USERNAME started_utc=$([DateTime]::UtcNow.ToString('o'))"
& $python -I -B tools\pr03b\product\test_backend.py browser 2>&1 | ForEach-Object { "$_" }
$code = $LASTEXITCODE
"backend_browser_exit=$code ended_utc=$([DateTime]::UtcNow.ToString('o'))"
exit $code
