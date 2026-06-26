@echo off
setlocal
cd /d "%~dp0"

echo.
echo ================================================================
echo   MTAP Installer Builder
echo ================================================================
echo.

REM ── Locate Inno Setup ────────────────────────────────────────────────────────
set ISCC=
for %%P in (
    "C:\Program Files (x86)\Inno Setup 6\iscc.exe"
    "C:\Program Files\Inno Setup 6\iscc.exe"
    "C:\Program Files (x86)\Inno Setup 5\iscc.exe"
    "C:\Program Files\Inno Setup 5\iscc.exe"
) do (
    if exist %%P ( set ISCC=%%~P & goto :found )
)
echo [ERROR] Inno Setup not found. Download from https://jrsoftware.org/isdl.php
pause & exit /b 1

:found
echo   Inno Setup : %ISCC%

REM ── Check dist\MTAP.exe ──────────────────────────────────────────────────────
if not exist "dist\MTAP.exe" (
    echo.
    echo [ERROR] dist\MTAP.exe not found — build it first:
    echo     python -m PyInstaller MTAP.spec --noconfirm
    echo.
    pause & exit /b 1
)

if not exist installer mkdir installer

echo.
echo Building MTAP_Setup.iss ...
"%ISCC%" MTAP_Setup.iss
if errorlevel 1 ( echo [ERROR] Inno Setup failed. & pause & exit /b 1 )

echo.
echo ================================================================
echo   Done!  Installer: installer\MTAP_Setup_0.1.0.exe
echo ================================================================
echo.
pause
