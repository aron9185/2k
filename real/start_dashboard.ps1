param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8765,
    [string]$Sports = "mlb,nba,nhl,wnba,golf",
    [switch]$NoSoccer,
    [int]$RefreshSeconds = 0,
    [switch]$RefreshOnStart
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$cacheDir = Join-Path $scriptDir ".cache\dashboard"
$pidFile = Join-Path $cacheDir "dashboard_server.pid"
$logFile = Join-Path $cacheDir "dashboard_server.out.log"
$errorLogFile = Join-Path $cacheDir "dashboard_server.err.log"
$serverScript = Join-Path $scriptDir "dashboard_server.py"

New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null

function Test-DashboardResponding {
    param([int]$Port)
    try {
        $status = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:${Port}/api/status" -TimeoutSec 2
        return ($status.sports -ne $null -and $status.running -ne $null)
    } catch {
        return $false
    }
}

if (Test-DashboardResponding -Port $Port) {
    Write-Output "Real Sports dashboard is already running at http://$HostAddress`:$Port."
    Write-Output "Run real\stop_dashboard.ps1 first if you want a clean restart."
    exit 0
}

$arguments = @(
    "-B",
    $serverScript,
    "--host", $HostAddress,
    "--port", [string]$Port,
    "--sports", $Sports,
    "--refresh-seconds", [string]$RefreshSeconds
)

if (-not $NoSoccer) {
    $arguments += "--refresh-soccer"
}
if ($RefreshOnStart) {
    $arguments += "--refresh-on-start"
}

$pythonCommand = (Get-Command python3 -ErrorAction Stop).Source

$process = Start-Process `
    -FilePath $pythonCommand `
    -ArgumentList $arguments `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError $errorLogFile `
    -PassThru

$process.Id | Set-Content -Path $pidFile -Encoding ASCII

Write-Output "Real Sports dashboard started at http://$HostAddress`:$Port"
Write-Output "PID: $($process.Id)"
Write-Output "Logs: $logFile"
Write-Output "Errors: $errorLogFile"
Write-Output "Watch logs: Get-Content -Path '$logFile' -Wait -Tail 80"
