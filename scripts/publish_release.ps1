# Log-to-LLM Sentinel Release Script
# Semantic Versioning (MAJOR.MINOR.PATCH): the version lives in version.txt and is bumped here.
#
#   .\scripts\publish_release.ps1 -Bump patch -DryRun   # preview only, nothing is modified
#   .\scripts\publish_release.ps1 -Bump minor           # new backward-compatible features
#   .\scripts\publish_release.ps1 -Bump major           # breaking changes
#   .\scripts\publish_release.ps1 -Version 1.4.0        # explicit version
#
# The "## [Unreleased]" section of CHANGELOG.md becomes "## [X.Y.Z] - YYYY-MM-DD" and is used
# as the GitHub release notes.

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("patch", "minor", "major")]
    [string]$Bump = "patch",

    [Parameter(Mandatory=$false)]
    [string]$Version,

    [Parameter(Mandatory=$false)]
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$utf8NoBom = New-Object System.Text.UTF8Encoding $False
$Image = "aschefr/log-to-llm-sentinel"

function Invoke-Native {
    # Runs a native command and stops the release if it fails (PS 5.1 does not do it by itself).
    # Success is judged on the exit code only: git/docker write progress and warnings to stderr,
    # which PS 5.1 would turn into terminating errors under "Stop" when the output is redirected.
    param([string]$Description, [scriptblock]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: $Description failed (exit code $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
}

Write-Host "`n=== Log-to-LLM Sentinel Release ===" -ForegroundColor Cyan

# --- Preconditions ---------------------------------------------------------
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne "main") {
    Write-Host "ERROR: releases are published from 'main' (current branch: $branch)." -ForegroundColor Red
    exit 1
}
Invoke-Native "git fetch" { git fetch --quiet origin }
$behind = (git rev-list --count "HEAD..origin/main").Trim()
if ([int]$behind -gt 0) {
    Write-Host "ERROR: local main is $behind commit(s) behind origin/main. Pull first." -ForegroundColor Red
    exit 1
}

# --- Version -----------------------------------------------------------------
$current = ([System.IO.File]::ReadAllText((Join-Path $PWD "version.txt"))).Trim()
if ($current -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
    Write-Host "ERROR: version.txt does not contain a MAJOR.MINOR.PATCH version ('$current')." -ForegroundColor Red
    exit 1
}
$major = [int]$Matches[1]; $minor = [int]$Matches[2]; $patch = [int]$Matches[3]

if ($Version) {
    if ($Version -notmatch '^\d+\.\d+\.\d+$') {
        Write-Host "ERROR: -Version must be MAJOR.MINOR.PATCH (got '$Version')." -ForegroundColor Red
        exit 1
    }
    $NewVersion = $Version
} elseif ($Bump -eq "major") {
    $NewVersion = "$($major + 1).0.0"
} elseif ($Bump -eq "minor") {
    $NewVersion = "$major.$($minor + 1).0"
} else {
    $NewVersion = "$major.$minor.$($patch + 1)"
}
$Tag = "v$NewVersion"

if (git tag --list $Tag) {
    Write-Host "ERROR: tag $Tag already exists." -ForegroundColor Red
    exit 1
}
Write-Host "  Version: $current -> $NewVersion" -ForegroundColor Green

# --- CHANGELOG -----------------------------------------------------------------
$changelogPath = Join-Path $PWD "CHANGELOG.md"
$changelog = [System.IO.File]::ReadAllText($changelogPath, [System.Text.Encoding]::UTF8)
$section = [regex]::Match($changelog, '(?s)## \[Unreleased\][^\n]*\n(.*?)(?=\r?\n## \[|\z)')
if (-not $section.Success -or -not $section.Groups[1].Value.Trim()) {
    Write-Host "ERROR: CHANGELOG.md has no '## [Unreleased]' section with content." -ForegroundColor Red
    exit 1
}
$notes = $section.Groups[1].Value.Trim()
$date = Get-Date -Format "yyyy-MM-dd"

$dirty = git status --porcelain
if ($dirty) {
    Write-Host "  Uncommitted changes that will be part of the release commit:" -ForegroundColor Yellow
    $dirty | ForEach-Object { Write-Host "    $_" }
}

if ($DryRun) {
    Write-Host "`n[DRY RUN] Release notes for $Tag :" -ForegroundColor Yellow
    Write-Host $notes
    Write-Host "`n[DRY RUN] Nothing was modified." -ForegroundColor Yellow
    exit 0
}

# --- Commit, tag, push ---------------------------------------------------------
Write-Host "`n[1/4] Commit & tag..." -ForegroundColor Yellow
$changelog = $changelog.Remove($section.Index, "## [Unreleased]".Length).Insert($section.Index, "## [$NewVersion] - $date")
[System.IO.File]::WriteAllText($changelogPath, $changelog, $utf8NoBom)
[System.IO.File]::WriteAllText((Join-Path $PWD "version.txt"), "$NewVersion`n", $utf8NoBom)
Invoke-Native "git add" { git add -A }
Invoke-Native "git commit" { git commit --quiet -m "release: $Tag" }
Invoke-Native "git tag" { git tag -a $Tag -m "Sentinel $Tag" }

Write-Host "`n[2/4] Push to GitHub..." -ForegroundColor Yellow
Invoke-Native "git push" { git push origin main $Tag }

Write-Host "`n[3/4] GitHub release..." -ForegroundColor Yellow
$notesFile = Join-Path $env:TEMP "sentinel-release-notes.md"
[System.IO.File]::WriteAllText($notesFile, $notes, $utf8NoBom)
Invoke-Native "gh release create" { gh release create $Tag --title "Sentinel $Tag" --notes-file $notesFile }
Remove-Item $notesFile -ErrorAction SilentlyContinue

Write-Host "`n[4/4] Docker build & push..." -ForegroundColor Yellow
Invoke-Native "docker build" { docker build -t "${Image}:$NewVersion" -t "${Image}:latest" . }
Invoke-Native "docker push $NewVersion" { docker push "${Image}:$NewVersion" }
Invoke-Native "docker push latest" { docker push "${Image}:latest" }

Write-Host "`n=== Release $Tag complete! ===" -ForegroundColor Green
Write-Host "  GitHub: https://github.com/Aschefr/log-to-llm-sentinel/releases/tag/$Tag"
Write-Host "  Docker: https://hub.docker.com/r/$Image"
