param(
    [int]$Port = 8765,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$cacheDir = Join-Path $scriptDir ".cache\dashboard"
$pidFile = Join-Path $cacheDir "dashboard_server.pid"

function Clear-DashboardPidFile {
    param([string]$Path)
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (Test-Path $Path) {
        Set-Content -Path $Path -Value "" -Encoding ASCII -ErrorAction SilentlyContinue
    }
}

function Test-DashboardResponding {
    param([int]$Port)
    try {
        $status = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:${Port}/api/status" -TimeoutSec 2
        return ($status.sports -ne $null -and $status.running -ne $null)
    } catch {
        return $false
    }
}

function Get-ListeningPids {
    param([int]$Port)
    $lines = netstat -ano | Select-String ":$Port\s+.*LISTENING"
    $pids = @()
    foreach ($line in $lines) {
        $parts = ($line.Line.Trim() -split "\s+")
        if ($parts.Count -ge 5) {
            $pids += [int]$parts[-1]
        }
    }
    return $pids | Sort-Object -Unique
}

$wasResponding = Test-DashboardResponding -Port $Port
$shutdownSent = $false
if ($wasResponding) {
    try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:${Port}/api/shutdown" -TimeoutSec 3 | Out-Null
        $shutdownSent = $true
        Start-Sleep -Milliseconds 900
    } catch {
        $shutdownSent = $false
    }
}

if (Test-Path $pidFile) {
    $pidText = (Get-Content -Path $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($pidText) {
        $process = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
        if ($process) {
            for ($i = 0; $i -lt 8; $i++) {
                Start-Sleep -Milliseconds 250
                $process = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
                if (-not $process) {
                    break
                }
            }
            if ($process -and $Force) {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Clear-DashboardPidFile -Path $pidFile
}

if ($wasResponding -and -not $shutdownSent) {
    $listenerPids = Get-ListeningPids -Port $Port
    foreach ($listenerPid in $listenerPids) {
        Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
    }
    if ($listenerPids.Count -gt 0) {
        Write-Output "Stopped dashboard listener(s) on port ${Port}: $($listenerPids -join ', ')."
        exit 0
    }
}

if ($shutdownSent) {
    Write-Output "Dashboard shutdown requested on port $Port."
} elseif ($wasResponding) {
    Write-Output "Dashboard listener on port $Port was stopped."
} else {
    Write-Output "No Real Sports dashboard answered on port $Port."
}
