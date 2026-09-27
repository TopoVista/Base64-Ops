$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$client = Join-Path $root "client"
$python = Join-Path $backend "venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
Push-Location $backend
& $python -m pytest -q
& $python -m ruff check .
& $python -m evals.runner --mode deterministic
& $python -m evals.runner --mode deterministic --output ..\artifacts\eval-summary.json --format json
& $python -m evals.runner --mode deterministic --category ci
& $python -c "from app.agent.graph import AgentGraph; print(type(AgentGraph().graph).__name__)"
Pop-Location
Push-Location $client
npx tsc --noEmit
npm run build
Pop-Location
Write-Host "Base64 Ops verification completed." -ForegroundColor Green
