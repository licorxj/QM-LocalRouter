#Requires -Version 5.1
<#
.SYNOPSIS
  Build the LocalRouter Windows installer (Inno Setup .exe).

.DESCRIPTION
  Pipeline:
    1. Build the frontend (npm ci + npm run build)          [-SkipFrontend]
    2. Write version.json (git tag/commit) for the frozen exe
    3. PyInstaller one-file build (dist\apirouter.exe)      [-SkipBackend]
    4. Stage release-stage\ (exe + run.bat + frontend\dist + .env.example)
    5. Compile scripts\windows-installer.iss with ISCC.exe

  Output: dist\installer\LocalRouter-Setup-<version>-x64.exe
  The installer installs per-user (no admin) into
  %LOCALAPPDATA%\Programs\LocalRouter because the frozen exe keeps all
  runtime state (sqlite db, logs, .env) next to itself.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build-windows-installer.ps1

.EXAMPLE
  scripts\build-windows-installer.ps1 -SkipFrontend -SkipBackend -Version 2.0.6
#>
param(
    [switch]$SkipFrontend,
    [switch]$SkipBackend,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Info([string]$m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Fail([string]$m) { Write-Host "[ERROR] $m" -ForegroundColor Red; exit 1 }

# PS 5.1: with $ErrorActionPreference="Stop", redirected native stderr becomes
# error records that terminate the script (litellm/git routinely warn on
# stderr), so probes run with a relaxed preference inside these helpers.
function Test-Native([string]$FilePath, [string[]]$ArgumentList) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @ArgumentList *> $null
        return ($LASTEXITCODE -eq 0)
    } finally { $ErrorActionPreference = $prev }
}
function Get-NativeOutput([string]$FilePath, [string[]]$ArgumentList, [string]$Default = "") {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = (& $FilePath @ArgumentList 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { return $Default }
        return $out
    } finally { $ErrorActionPreference = $prev }
}

# ---------------------------------------------------------------- 1. Version
$tagRaw = ""
if ([string]::IsNullOrWhiteSpace($Version)) {
    $tagRaw = Get-NativeOutput "git" @("describe", "--tags", "--always")
    if ([string]::IsNullOrWhiteSpace($tagRaw)) { $tagRaw = "0.0.0-unknown" }
    if ($tagRaw.StartsWith("v")) { $Version = $tagRaw.Substring(1) } else { $Version = $tagRaw }
} else {
    $tagRaw = "v$Version"
}
Info "Repo: $repo"
Info "Installer version: $Version"

# ---------------------------------------------------------------- 2. Python
$py = $null
foreach ($cand in @(
    (Join-Path $repo "backend\venv\Scripts\python.exe"),
    (Join-Path $repo "backend\.venv\Scripts\python.exe")
)) {
    if (Test-Path $cand) { $py = $cand; break }
}
if (-not $py) { $py = "python" }
Info "Python: $py"

# ---------------------------------------------------------------- 3. Frontend
if (-not $SkipFrontend) {
    Info "Building frontend (npm run build)..."
    Push-Location (Join-Path $repo "frontend")
    try {
        if (-not (Test-Path "node_modules")) {
            npm ci
            if ($LASTEXITCODE -ne 0) { Fail "npm ci failed" }
        }
        npm run build
        if ($LASTEXITCODE -ne 0) { Fail "npm run build failed" }
    } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $repo "frontend\dist\index.html"))) {
    Fail "frontend\dist\index.html not found. Build the frontend first (or drop -SkipFrontend)."
}

# ---------------------------------------------------------------- 4. version.json
$gitCommit = Get-NativeOutput "git" @("rev-parse", "--short", "HEAD")
$gitStatus = Get-NativeOutput "git" @("status", "--porcelain")
$dirty = ($gitStatus.Length -gt 0)
$versionObj = [ordered]@{
    git_tag    = $tagRaw
    git_commit = $gitCommit
    git_dirty  = $dirty
}
# UTF-8 without BOM: the frozen exe parses this with plain json.
[System.IO.File]::WriteAllText(
    (Join-Path $repo "version.json"),
    ($versionObj | ConvertTo-Json),
    (New-Object System.Text.UTF8Encoding($false))
)

# ---------------------------------------------------------------- 5. PyInstaller
if (-not $SkipBackend) {
    if (-not (Test-Native $py @("-c", "import litellm, webview"))) {
        Info "Backend deps missing in selected Python; installing backend/requirements.txt..."
        & $py -m pip install -r (Join-Path $repo "backend\requirements.txt")
        if ($LASTEXITCODE -ne 0) { Fail "pip install backend/requirements.txt failed" }
    }
    if (-not (Test-Native $py @("-m", "PyInstaller", "--version"))) {
        Info "Installing build requirements (pyinstaller)..."
        & $py -m pip install -r (Join-Path $repo "requirements-build.txt")
        if ($LASTEXITCODE -ne 0) { Fail "pip install requirements-build.txt failed" }
    }
    Info "PyInstaller one-file build..."
    & $py -m PyInstaller (Join-Path $repo "build_pyinstaller.spec") --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed" }
}
if (-not (Test-Path (Join-Path $repo "dist\apirouter.exe"))) {
    Fail "dist\apirouter.exe not found. Run PyInstaller first (or drop -SkipBackend)."
}
if (-not (Test-Path (Join-Path $repo "dist\LocalRouter.exe"))) {
    Fail "dist\LocalRouter.exe not found - the desktop shell exe is built by build_pyinstaller.spec."
}

# ---------------------------------------------------------------- 6. Stage
$stage = Join-Path $repo "release-stage"
Info "Staging files into $stage"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item (Join-Path $repo "dist\apirouter.exe") $stage
Copy-Item (Join-Path $repo "dist\LocalRouter.exe") $stage
Copy-Item (Join-Path $repo "release\run.bat") $stage
New-Item -ItemType Directory -Path (Join-Path $stage "frontend") | Out-Null
Copy-Item -Recurse (Join-Path $repo "frontend\dist") (Join-Path $stage "frontend\dist")
if (Test-Path (Join-Path $repo ".env.example")) {
    Copy-Item (Join-Path $repo ".env.example") $stage
}

# ---------------------------------------------------------------- 7. Icon (optional)
# frontend\public\logo.png -> build\app.ico via Pillow; skipped silently when
# Pillow is unavailable (installer then uses Inno Setup's default icon).
$ico = Join-Path $repo "build\app.ico"
$icoOk = $false
$logo = Join-Path $repo "frontend\public\logo.png"
if (Test-Path $logo) {
    New-Item -ItemType Directory -Force -Path (Join-Path $repo "build") | Out-Null
    $pyScript = "from PIL import Image; im = Image.open(r'$logo').convert('RGBA'); im.save(r'$ico', format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
    if ((Test-Native $py @("-c", $pyScript)) -and (Test-Path $ico)) {
        $icoOk = $true
        Copy-Item $ico (Join-Path $stage "app.ico") -Force
        Info "Installer icon generated from frontend\public\logo.png"
    } else {
        Write-Host "    (Pillow unavailable - using the default installer icon)"
    }
}

# ---------------------------------------------------------------- 8. Inno Setup (ISCC)
$iscc = $null
$isccCandidates = @()
if ($env:INNO_SETUP_DIR) { $isccCandidates += (Join-Path $env:INNO_SETUP_DIR "ISCC.exe") }
$pf86 = ${env:ProgramFiles(x86)}
if (-not $pf86) { $pf86 = "C:\Program Files (x86)" }
$isccCandidates += (Join-Path $pf86 "Inno Setup 6\ISCC.exe")
if ($env:ProgramFiles) { $isccCandidates += (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe") }
# winget's default per-user install location
if ($env:LOCALAPPDATA) { $isccCandidates += (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe") }
foreach ($cand in $isccCandidates) {
    if (Test-Path $cand) { $iscc = $cand; break }
}
if (-not $iscc) {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc -and $env:CI) {
    Info "ISCC.exe not found; installing Inno Setup via Chocolatey (CI fallback)..."
    choco install innosetup -y --no-progress
    if ($LASTEXITCODE -ne 0) { Fail "choco install innosetup failed" }
    foreach ($cand in $isccCandidates) {
        if (Test-Path $cand) { $iscc = $cand; break }
    }
}
if (-not $iscc) {
    Fail "Inno Setup 6 not found. Install it (winget install JRSoftware.InnoSetup) or set INNO_SETUP_DIR to its install folder."
}
Info "ISCC: $iscc"

# ---------------------------------------------------------------- 9. Compile
$isccArgs = @("/DAppVersion=$Version", "/DStageDir=$stage")
if ($icoOk) { $isccArgs += "/DSetupIconFile=$ico" }
$isccArgs += (Join-Path $repo "scripts\windows-installer.iss")

# ISCC can fail transiently when antivirus holds the freshly written setup
# exe (seen on Windows Defender); retry a couple of times.
$compiled = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    & $iscc @isccArgs
    if ($LASTEXITCODE -eq 0) { $compiled = $true; break }
    Write-Host "    ISCC attempt $attempt failed (often a transient antivirus file lock); retrying..."
    Start-Sleep -Seconds 3
}
if (-not $compiled) { Fail "Inno Setup compile failed after 3 attempts" }

$installer = Join-Path $repo "dist\installer\LocalRouter-Setup-$Version-x64.exe"
if (-not (Test-Path $installer)) { Fail "Installer not produced: $installer" }
Info "Done: $installer"
