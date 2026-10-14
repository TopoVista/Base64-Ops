[CmdletBinding()]
param(
    [int]$Port = 4173,
    [string]$ChromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe",
    [switch]$SkipBuild
)

<#
.SYNOPSIS
Captures the public, mutation-disabled Safe Repair fixture for documentation.

.DESCRIPTION
Builds the Vite client (unless -SkipBuild is supplied), starts an isolated
preview server, waits for /demo, then asks headless Chromium to render the
fixture after its deterministic walkthrough has settled. It never authenticates
with Clerk, contacts GitHub, or starts the backend. The output is intentionally
committed as documentation evidence, not as a claim about a live provider.
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$client = Join-Path $root "client"
$outputDirectory = Join-Path $root "docs\assets\screenshots"
$outputPath = Join-Path $outputDirectory "safe-repair-demo.png"

if (-not (Test-Path -LiteralPath $ChromePath)) {
    throw "Chrome was not found at '$ChromePath'. Supply -ChromePath with a Chromium-compatible executable."
}

New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

if (-not $SkipBuild) {
    Push-Location $client
    try {
        npm run build
    }
    finally {
        Pop-Location
    }
}

$preview = $null
$chromeProfile = Join-Path $env:TEMP ("base64-ops-demo-capture-" + [Guid]::NewGuid().ToString("N"))
try {
    $preview = Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c", "npx vite preview --host 127.0.0.1 --port $Port --strictPort" `
        -WorkingDirectory $client `
        -WindowStyle Hidden `
        -PassThru

    $demoUri = "http://127.0.0.1:$Port/demo"
    $deadline = (Get-Date).AddSeconds(30)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing $demoUri -TimeoutSec 3
            if ($response.StatusCode -eq 200) { break }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)

    if (-not $response -or $response.StatusCode -ne 200) {
        throw "The local demo preview did not become ready within 30 seconds."
    }

    $chrome = Start-Process -FilePath $ChromePath `
        -ArgumentList "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", `
        "--user-data-dir=$chromeProfile", "--virtual-time-budget=20000", "--window-size=1440,1100", `
        "--screenshot=$outputPath", $demoUri `
        -WindowStyle Hidden `
        -PassThru
    $null = $chrome.WaitForExit(45000)

    if (-not (Test-Path -LiteralPath $outputPath) -or (Get-Item -LiteralPath $outputPath).Length -lt 20000) {
        throw "Chromium did not produce a usable demo screenshot at '$outputPath'."
    }

    Write-Host "Captured deterministic Safe Repair demo: $outputPath"
}
finally {
    if ($preview -and -not $preview.HasExited) {
        Stop-Process -Id $preview.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $chromeProfile -Recurse -Force -ErrorAction SilentlyContinue
}
