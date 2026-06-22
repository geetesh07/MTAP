@echo off
REM ============================================================
REM  MTAP - Fast code-only rebuild
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo  MTAP  -  Fast Rebuild   %date% %time%
echo ============================================================

echo [1/3] Stopping any running MTAP...
taskkill /F /IM MTAP.exe >nul 2>&1
taskkill /F /IM accoreconsole.exe >nul 2>&1
timeout /t 3 /nobreak >nul

echo [2/3] Clearing dist...
if exist "dist_old" rd /s /q "dist_old" >nul 2>&1
if exist "dist"     ren "dist" "dist_old" >nul 2>&1

echo [3/3] Running PyInstaller...
python -m PyInstaller MTAP.spec --noconfirm
if errorlevel 1 (
    echo.
    echo  *** BUILD FAILED - see output above ***
    pause
    exit /b 1
)

if exist "dist_old" rd /s /q "dist_old" >nul 2>&1

echo.
echo ============================================================
for /f "tokens=1" %%A in ('powershell -NoProfile -Command "(Get-ChildItem 'dist' -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB -as [int]"') do echo   Total size : %%A MB
echo   EXE path   : %CD%\dist\MTAP.exe
echo ============================================================
echo  Done. Opening output folder...
start "" "%CD%\dist"
endlocal
