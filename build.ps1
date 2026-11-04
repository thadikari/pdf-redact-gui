# Builds dist\RedactPDF.exe - a single portable executable (no Python needed on the target PC).
# Usage (PowerShell, from the repo folder):   .\build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Run($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$exe failed with exit code $LASTEXITCODE" }
}

if (-not (Test-Path .venv)) { Run python @("-m", "venv", ".venv") }
$py = ".\.venv\Scripts\python.exe"
Run $py @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", "requirements-dev.txt")

# app icon (drawn in code) -> build\icon.ico
New-Item -ItemType Directory -Force build | Out-Null
$env:QT_QPA_PLATFORM = "offscreen"
Run $py @("-c", "from PySide6.QtGui import QGuiApplication; app = QGuiApplication([]); from redact_pdf.icons import write_ico; write_ico('build/icon.ico')")
Remove-Item Env:QT_QPA_PLATFORM

Run $py @("-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
          "--name", "RedactPDF", "--icon", "build\icon.ico",
          "--exclude-module", "tkinter", "--exclude-module", "unittest", "--exclude-module", "pytest",
          "--exclude-module", "PySide6.QtNetwork", "--exclude-module", "PySide6.QtQml",
          "main.py")

$exe = Get-Item dist\RedactPDF.exe
"Built {0} ({1:N1} MB)" -f $exe.FullName, ($exe.Length / 1MB)
