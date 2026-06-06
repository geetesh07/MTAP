@echo off
REM ============================================================
REM  MTAP - One-click EXE builder
REM  Produces:  dist\MTAP.exe   (single self-contained file)
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo   MTAP  -  Building one-click EXE
echo ============================================================
echo.

REM --- 1. Install / update dependencies -----------------------
echo [1/4] Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

REM --- 2. Make sure PyInstaller is available ------------------
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 goto :error
)

REM --- 3. Generate the app icon ------------------------------
echo [2/4] Generating app icon...
python build_icon.py

set "ICON="
if exist "assets\icons\mtap.ico" set "ICON=--icon assets\icons\mtap.ico"

REM --- 4. Clean old build ------------------------------------
echo [3/4] Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

REM --- 5. Build the EXE using the spec file ------------------
echo [4/4] Building EXE with PyInstaller (this can take a few minutes)...
echo        UPX is OFF to avoid Windows Defender false positives.
python -m PyInstaller --noconfirm MTAP.spec
if errorlevel 1 goto :error

echo Done.
echo.
echo ============================================================
echo   SUCCESS!   Your EXE is here:
echo       %cd%\dist\MTAP.exe
echo ============================================================
echo.
start "" "%cd%\dist"
goto :end

:error
echo.
echo ************************************************************
echo   BUILD FAILED - scroll up to see the error.
echo ************************************************************
echo.

:end
pause
endlocal
