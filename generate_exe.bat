@echo off
REM ============================================================
REM  MTAP - Full clean build
REM
REM  Output: dist\MTAP.exe  (exe + _internal\ directly in dist\)
REM
REM  For a fast code-only rebuild run fast.bat instead.
REM ============================================================
setlocal
cd /d "%~dp0"

set DIST=dist

echo.
echo ============================================================
echo  MTAP  -  Full Build   %date% %time%
echo ============================================================

echo [1/3] Stopping any running MTAP...
taskkill /F /IM MTAP.exe >nul 2>&1
taskkill /F /IM accoreconsole.exe >nul 2>&1

echo [2/3] Wiping dist\ for a clean build...
if exist "%DIST%" rmdir /S /Q "%DIST%"

echo [3/3] Running PyInstaller...
python -m PyInstaller MTAP.spec --noconfirm
if errorlevel 1 (
    echo.
    echo  *** BUILD FAILED - see output above ***
    pause
    exit /b 1
)

echo.
echo ============================================================
for /f "tokens=1" %%A in ('powershell -NoProfile -Command "(Get-ChildItem '%DIST%' -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB -as [int]"') do echo   Total size : %%A MB
echo   EXE path   : %CD%\%DIST%\MTAP.exe
echo ============================================================
echo  Done. Opening output folder...
start "" "%CD%\%DIST%"
endlocal
