# Log-to-LLM Sentinel Release Script
# Auto-calculates version based on git commits

param(
    [Parameter(Mandatory=$false)]
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "`n=== Log-to-LLM Sentinel Release ===" -ForegroundColor Cyan

$dirty = git status --porcelain 2>&1
if ($dirty) {
    Write-Host "INFO: Uncommitted changes will be included in the release commit:" -ForegroundColor Yellow
    Write-Host $dirty
}

Write-Host "`n[1/5] Git commit..." -ForegroundColor Yellow
if (-not $DryRun) {
    git add -A
    git commit -m "release: automatic version bump" 2>&1 | Out-Null
}

Write-Host "`n[2/5] Calculating version..." -ForegroundColor Yellow
$merges = (git rev-list --merges --count HEAD).Trim()
$commits = (git rev-list --count HEAD).Trim()
$mergesInt = [int]$merges + 2
$Version = "1.${mergesInt}.${commits}"

$utf8NoBom = New-Object System.Text.UTF8Encoding $False
[System.IO.File]::WriteAllText((Join-Path $PWD "version.txt"), $Version, $utf8NoBom)

Write-Host "  Calculated version: $Version" -ForegroundColor Green

if (-not $DryRun) {
    git add version.txt
    git commit --amend --no-edit 2>&1 | Out-Null
}

if ($DryRun) {
    Write-Host "`n[DRY RUN] Stopping before push & docker operations." -ForegroundColor Yellow
    exit 0
}

Write-Host "`n[3/5] Git tag & push..." -ForegroundColor Yellow
git tag -a "v$Version" -m "Sentinel v$Version"
git push origin main --tags 2>&1 | ForEach-Object { Write-Host "  $_" }
Write-Host "  Pushed to GitHub" -ForegroundColor Green

Write-Host "`n[4/5] Creating GitHub release..." -ForegroundColor Yellow
gh release delete "v$Version" -y 2>&1 | Out-Null
gh release create "v$Version" --title "Sentinel v$Version" --generate-notes

Write-Host "`n[5/5] Building and pushing Docker image..." -ForegroundColor Yellow
docker build -t aschefr/log-to-llm-sentinel:$Version -t aschefr/log-to-llm-sentinel:latest .
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker build failed" -ForegroundColor Red
    exit 1
}

docker push aschefr/log-to-llm-sentinel:$Version
docker push aschefr/log-to-llm-sentinel:latest

Write-Host "`n=== Release v$Version complete! ===" -ForegroundColor Green
$releaseUrl = "https://github.com/Aschefr/log-to-llm-sentinel/releases/tag/v$Version"
Write-Host "  GitHub: $releaseUrl"
Write-Host "  Docker: https://hub.docker.com/r/aschefr/log-to-llm-sentinel"
