$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================="
Write-Host " Chess Move Reader - Build and Install"
Write-Host "========================================="
Write-Host ""

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Set-Location $ProjectDir

$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$ExePath = Join-Path $ProjectDir "dist\ChessMoveReader.exe"
$IconPath = Join-Path $ProjectDir "chess.ico"

# =========================================================
# CHECK REQUIRED FILES
# =========================================================

if (!(Test-Path (Join-Path $ProjectDir "main.py"))) {
    Write-Host "ERROR: main.py not found."
    exit 1
}

if (!(Test-Path (Join-Path $ProjectDir "requirements.txt"))) {
    Write-Host "ERROR: requirements.txt not found."
    exit 1
}

if (!(Test-Path $IconPath)) {
    Write-Host "ERROR: chess.ico not found."
    exit 1
}

# =========================================================
# CREATE VIRTUAL ENVIRONMENT
# =========================================================

Write-Host "[1/6] Checking virtual environment..."

if (!(Test-Path $VenvPython)) {

    Write-Host "Creating .venv..."

    python -m venv .venv

    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to create virtual environment."
        exit 1
    }

} else {

    Write-Host ".venv already exists."

}

# =========================================================
# INSTALL REQUIREMENTS
# =========================================================

Write-Host ""
Write-Host "[2/6] Installing Python packages..."

& $VenvPython -m pip install --upgrade pip

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip upgrade failed."
    exit 1
}

& $VenvPython -m pip install -r requirements.txt

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: requirements installation failed."
    exit 1
}

# =========================================================
# INSTALL PYINSTALLER
# =========================================================

Write-Host ""
Write-Host "[3/6] Installing PyInstaller..."

& $VenvPython -m pip install pyinstaller

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: PyInstaller installation failed."
    exit 1
}

# =========================================================
# CLEAN OLD BUILD
# =========================================================

Write-Host ""
Write-Host "[4/6] Cleaning old build files..."

$BuildDir = Join-Path $ProjectDir "build"
$DistDir = Join-Path $ProjectDir "dist"
$SpecFile = Join-Path $ProjectDir "ChessMoveReader.spec"

if (Test-Path $BuildDir) {
    Remove-Item $BuildDir -Recurse -Force
}

if (Test-Path $DistDir) {
    Remove-Item $DistDir -Recurse -Force
}

if (Test-Path $SpecFile) {
    Remove-Item $SpecFile -Force
}

# =========================================================
# BUILD EXE
# =========================================================

Write-Host ""
Write-Host "[5/6] Building ChessMoveReader.exe..."

& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name ChessMoveReader `
    --icon="$IconPath" `
    main.py

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: PyInstaller build failed."
    exit 1
}

if (!(Test-Path $ExePath)) {
    Write-Host ""
    Write-Host "ERROR: EXE was not created."
    exit 1
}

# =========================================================
# CREATE DESKTOP SHORTCUT
# =========================================================

Write-Host ""
Write-Host "[6/6] Creating Desktop shortcut..."

$DesktopPath = [Environment]::GetFolderPath("Desktop")

$ShortcutPath = Join-Path `
    $DesktopPath `
    "Chess Move Reader.lnk"

$Shell = New-Object -ComObject WScript.Shell

$Shortcut = $Shell.CreateShortcut($ShortcutPath)

$Shortcut.TargetPath = $ExePath

$Shortcut.WorkingDirectory = $ProjectDir

$Shortcut.Description = "Chess Move Reader"

$Shortcut.IconLocation = "$ExePath,0"

$Shortcut.Save()

# =========================================================
# DONE
# =========================================================

Write-Host ""
Write-Host "========================================="
Write-Host " BUILD SUCCESSFUL"
Write-Host "========================================="
Write-Host ""

Write-Host "EXE:"
Write-Host $ExePath
Write-Host ""

Write-Host "Desktop shortcut:"
Write-Host $ShortcutPath
Write-Host ""

Write-Host "You can now double-click:"
Write-Host "Chess Move Reader.lnk"
Write-Host ""

Read-Host "Press Enter to exit"