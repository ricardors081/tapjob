# TapJob Local Guards Installer
# Configures local repository git hooks path without touching global config.

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

Write-Host "Configuring local repository git hooksPath to .githooks..." -ForegroundColor Cyan

# Set local git hooksPath (strictly --local, NEVER --global)
git config --local core.hooksPath .githooks

# Verify configuration
$configuredHooksPath = git config --local core.hooksPath

if ($configuredHooksPath -eq ".githooks") {
    Write-Host "Successfully verified git config --local core.hooksPath = .githooks" -ForegroundColor Green
    exit 0
} else {
    Write-Error "Failed to verify core.hooksPath. Expected '.githooks', got '$configuredHooksPath'"
    exit 1
}
