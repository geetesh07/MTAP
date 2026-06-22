@echo off
REM ============================================================
REM  MTAP - Fast code-only rebuild
REM
REM  Builds to dist_new\ to avoid locked DLL errors, then swaps
REM  dist_new <-> dist so MTAP.exe is always at dist\MTAP.exe.
REM  Run generate_exe.bat for a full clean build.
REM ============================================================
setlocal
cd /d "%~dp0"

set DIST=dist
set DISTNEW=dist_new
set DISTOLD=dist_old

echo.
echo ============================================================
echo  MTAP  -  Fast Rebuild   %date% %time%
echo ============================================================

echo [1/4] Stopping any running MTAP...
taskkill /F /IM MTAP.exe >nul 2>&1
taskkill /F /IM accoreconsole.exe >nul 2>&1
timeout /t 3 /nobreak >nul

echo [2/4] Clearing previous build targets...
if exist "%DISTNEW%"     rd /s /q "%DISTNEW%"  >nul 2>&1
if exist "%DISTOLD%"     rd /s /q "%DISTOLD%"  >nul 2>&1

echo [3/4] Running PyInstaller (output -> %DISTNEW%)...
python -m PyInstaller MTAP.spec --noconfirm --distpath "%DISTNEW%"
if errorlevel 1 (
    echo.
    echo  *** BUILD FAILED - see output above ***
    pause
    exit /b 1
)

echo [4/4] Swapping dist...
REM Rename old dist out of the way (may still be partially locked — rename works)
if exist "%DIST%"    ren "%DIST%" "%DISTOLD%"    >nul 2>&1
REM Move new build into place
if exist "%DISTNEW%" ren "%DISTNEW%" "%DIST%"    >nul 2>&1
REM Delete old dist in background (locks usually gone now that process was killed)
if exist "%DISTOLD%" rd /s /q "%DISTOLD%"        >nul 2>&1

echo.
echo ============================================================
for /f "tokens=1" %%A in ('powershell -NoProfile -Command "(Get-ChildItem '%DIST%' -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB -as [int]"') do echo   Total size : %%A MB
echo   EXE path   : %CD%\%DIST%\MTAP.exe
echo ============================================================
echo  Done. Opening output folder...
start "" "%CD%\%DIST%"
endlocal
