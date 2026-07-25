<#
.SYNOPSIS
    One-shot uploader for the Agentic DFIR Platform repo.
    Initializes git, commits everything, creates the GitHub repo, and pushes.

.DESCRIPTION
    Run this from inside the repository folder (the folder that contains README.md).
    It is safe to re-run: if the repo already exists on GitHub it just pushes new commits.

    Prerequisites (installed once — see the README / chat walkthrough):
        winget install Git.Git GitHub.cli
        gh auth login

.PARAMETER RepoName
    Name of the GitHub repository to create. Default: dfir-agentic-soc-platform

.PARAMETER Description
    Repo description shown on GitHub.

.PARAMETER Visibility
    public or private. Default: public (it's a portfolio piece).

.PARAMETER ExtraScreenshots
    Optional folder of additional .png files to copy into images/ before committing.
    Use this if you add more screenshots later. Files are copied verbatim (no renaming),
    so name them the way you want them referenced.

.EXAMPLE
    .\publish-to-github.ps1
    .\publish-to-github.ps1 -RepoName my-soc-lab -Visibility private
#>

param(
    [string]$RepoName         = "dfir-agentic-soc-platform",
    [string]$Description      = "4-VM DFIR platform: QRadar SIEM -> Suricata/Zeek NSM -> DFIR-IRIS -> autonomous multi-agent AI SOC (Claude Agent SDK) that closes the loop.",
    [ValidateSet("public","private")]
    [string]$Visibility       = "public",
    [string]$ExtraScreenshots = ""
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }
function Fail($msg)       { Write-Host "`n[ERROR] $msg" -ForegroundColor Red; exit 1 }

# --- Work from the folder this script lives in -----------------------------
Set-Location -Path $PSScriptRoot
Write-Step "Working directory: $PSScriptRoot"

if (-not (Test-Path ".\README.md")) {
    Fail "README.md not found here. Run this script from inside the repository folder."
}

# --- Preflight: git + gh installed -----------------------------------------
Write-Step "Checking prerequisites"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "git not found. Install it first:  winget install Git.Git   (then reopen this terminal)."
}
Write-Ok "git found ($((git --version)))"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Fail "GitHub CLI not found. Install it first:  winget install GitHub.cli   (then reopen this terminal)."
}
Write-Ok "gh found ($((gh --version | Select-Object -First 1)))"

# --- Preflight: gh authenticated -------------------------------------------
Write-Step "Checking GitHub authentication"
gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "You are not logged in to GitHub. Launching 'gh auth login'..."
    gh auth login
    if ($LASTEXITCODE -ne 0) { Fail "gh auth login did not complete. Re-run this script after logging in." }
}
$ghUser = (gh api user --jq ".login" 2>$null)
if (-not $ghUser) { Fail "Could not read your GitHub username. Try 'gh auth login' again." }
Write-Ok "Authenticated as $ghUser"

# --- Optional: copy in extra screenshots -----------------------------------
if ($ExtraScreenshots -ne "") {
    Write-Step "Copying extra screenshots from $ExtraScreenshots"
    if (-not (Test-Path $ExtraScreenshots)) { Fail "ExtraScreenshots folder not found: $ExtraScreenshots" }
    New-Item -ItemType Directory -Force -Path ".\images" | Out-Null
    $pngs = Get-ChildItem -Path $ExtraScreenshots -Filter *.png -File
    foreach ($p in $pngs) { Copy-Item $p.FullName -Destination ".\images\" -Force }
    Write-Ok "Copied $($pngs.Count) file(s) into images\"
}

# --- Safety: make sure no secrets are about to be committed -----------------
Write-Step "Scanning for accidental secrets (*.env)"
$envFiles = Get-ChildItem -Recurse -File -Include *.env -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne "poller.env.example" }
if ($envFiles) {
    Write-Warn "Found .env file(s); these are gitignored and will NOT be pushed:"
    $envFiles | ForEach-Object { Write-Host "        $($_.FullName)" -ForegroundColor Yellow }
}
if (-not (Test-Path ".\.gitignore")) { Write-Warn ".gitignore missing — expected one in this repo." }

# --- git init / commit ------------------------------------------------------
Write-Step "Preparing local git repository"
if (-not (Test-Path ".\.git")) {
    git init | Out-Null
    Write-Ok "Initialized new git repo"
} else {
    Write-Ok "git repo already initialized"
}

git branch -M main 2>$null | Out-Null

# Identity (only sets if not already configured, and only locally)
if (-not (git config user.email 2>$null)) {
    git config user.email "$ghUser@users.noreply.github.com"
    git config user.name  "$ghUser"
    Write-Ok "Set local git identity to $ghUser"
}

git add -A
$pending = git status --porcelain
if ($pending) {
    git commit -m "Agentic DFIR platform: QRadar -> NSM -> DFIR-IRIS -> autonomous AI SOC" | Out-Null
    Write-Ok "Committed working tree"
} else {
    Write-Ok "Nothing new to commit"
}

# --- Create remote (if needed) and push ------------------------------------
$repoFull = "$ghUser/$RepoName"
Write-Step "Publishing to GitHub: $repoFull ($Visibility)"

$exists = $false
gh repo view $repoFull 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $exists = $true }

if ($exists) {
    Write-Warn "Repo $repoFull already exists — pushing to it."
    $remote = git remote 2>$null
    if ($remote -notcontains "origin") {
        git remote add origin "https://github.com/$repoFull.git"
    }
    git push -u origin main
    if ($LASTEXITCODE -ne 0) { Fail "Push failed. Check the messages above." }
} else {
    # Creates the repo, sets 'origin', and pushes 'main' in one step.
    gh repo create $RepoName --$Visibility --source=. --remote=origin --description "$Description" --push
    if ($LASTEXITCODE -ne 0) { Fail "gh repo create failed. Check the messages above." }
}

Write-Step "Done!"
Write-Host "    Repository: " -NoNewline; Write-Host "https://github.com/$repoFull" -ForegroundColor Green
Write-Host "    Opening it in your browser..."
gh repo view $repoFull --web 2>$null | Out-Null
