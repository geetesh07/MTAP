@echo off
REM ============================================================
REM  MTAP - Rebuild after editing a TEMPLATE or BLOCK .dwg
REM
REM  Run this whenever you change any DWG in autocad\blocks\
REM  (MTAP_TEMPLATE, MTAP_BACKTAPER, MTAP_GDT, MTAP_DATUM).
REM  It re-embeds the latest geometry into the app, then
REM  rebuilds dist\MTAP.exe so the packaged exe ships it.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   MTAP  -  Re-embedding blocks and rebuilding EXE
echo ============================================================
echo.

REM --- 1. Re-embed the DWG blocks into app\dxf\block_data.py ---
echo [1/3] Embedding block/template DWGs...
python tools\embed_blocks.py
if errorlevel 1 goto :error

REM --- 2. Close any running exe so the file isn't locked -------
echo [2/3] Closing any running MTAP.exe...
taskkill /F /IM MTAP.exe >nul 2>&1

REM --- 3. Rebuild the EXE from the spec -----------------------
echo [3/3] Building EXE with PyInstaller (this can take a minute)...
python -m PyInstaller --noconfirm MTAP.spec
if errorlevel 1 goto :error

echo.
echo ============================================================
echo   SUCCESS!   Updated EXE:
echo       %cd%\dist\MTAP.exe
echo ============================================================
echo.
goto :end

:error
echo.
echo ************************************************************
echo   FAILED - scroll up to see the error.
echo ************************************************************
echo.

:end
pause
endlocal
