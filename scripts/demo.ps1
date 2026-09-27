$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Write-Host "Base64 Ops deterministic demo setup" -ForegroundColor Cyan
foreach ($command in @("python", "node", "npm")) { if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Missing prerequisite: $command" } }
if (-not (Test-Path (Join-Path $root "backend\.env"))) { Write-Warning "Create backend/.env from backend/.env.example before starting authenticated services." }
if (-not (Test-Path (Join-Path $root "client\.env"))) { Write-Warning "Create client/.env from client/.env.example before starting the UI." }
Write-Host "Run .\scripts\verify.ps1, then follow docs\DEMO_SCRIPT.md."
