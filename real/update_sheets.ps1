param(
    [string[]]$Sports = @("mlb", "nba", "nhl"),
    [string]$MarketsCsv = "real\sportsbook_markets_consensus_live.csv",
    [string]$Season = "2025",
    [switch]$RefreshSoccer
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Command
    )
    Write-Host ">> $($Command -join ' ')" -ForegroundColor Cyan
    & $Command[0] $Command[1..($Command.Length - 1)]
}

$joinedSports = ($Sports -join ",")

Invoke-Step @(
    "python3",
    "-B",
    "real\ingest_public_markets.py",
    "--providers", "draftkings,fanduel",
    "--sports", $joinedSports,
    "--force-live",
    "--output", $MarketsCsv,
    "--dump-json-dir", "real\tmp\consensus_live_check"
)

if ($RefreshSoccer) {
    Invoke-Step @(
        "python3",
        "-B",
        "real\ingest_public_markets.py",
        "--providers", "draftkings",
        "--sports", "soccer",
        "--force-live",
        "--output", "real\sportsbook_markets_soccer_live.csv",
        "--dump-json-dir", "real\tmp\draftkings_soccer_live_check"
    )
}

foreach ($sport in $Sports) {
    $recommendationCsv = "real\poll_vote_recommendations_consensus_$sport.csv"
    Invoke-Step @(
        "python3",
        "-B",
        "real\recommend_game_feed_polls.py",
        "--sport", $sport,
        "--markets-csv", $MarketsCsv,
        "--output", $recommendationCsv
    )

    $firstRow = Import-Csv $recommendationCsv | Select-Object -First 1
    if ($null -ne $firstRow -and $firstRow.day) {
        Invoke-Step @(
            "python3",
            "-B",
            "real\lineup.py",
            "--sport", $sport,
            "--date", $firstRow.day,
            "--season", $Season
        )
    }

    if ($sport -in @("mlb", "nba", "nhl")) {
        Invoke-Step @(
            "python3",
            "-B",
            "real\render_vote_sheet.py",
            "--input", $recommendationCsv,
            "--not-started-only",
            "--refresh-predictions"
        )
    } else {
        Invoke-Step @(
            "python3",
            "-B",
            "real\render_vote_sheet.py",
            "--input", $recommendationCsv,
            "--not-started-only"
        )
    }
}

Write-Host ""
Write-Host "Latest vote sheets:" -ForegroundColor Green
Get-ChildItem real\output -Filter "*_v*.md" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 10 Name, LastWriteTime
