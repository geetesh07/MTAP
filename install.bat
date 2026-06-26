@echo off
setlocal
cd /d "%~dp0"

echo.
echo ================================================================
echo   MTAP Dependency Installer
echo ================================================================
echo.

REM ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo.
    echo   Download Python 3.11 from  https://www.python.org/downloads/
    echo   Make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%V in ('python --version 2^>^&1') do set PYVER=%%V
echo   Python : %PYVER%

REM ── Upgrade pip ──────────────────────────────────────────────────────────────
echo.
echo [1/5] Upgrading pip ...
python -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo [WARN] pip upgrade failed — continuing anyway.
)

REM ── Install packages ─────────────────────────────────────────────────────────
echo.
echo [2/5] Installing PyQt6 ...
python -m pip install PyQt6 --quiet
if errorlevel 1 ( echo [ERROR] PyQt6 install failed. & pause & exit /b 1 )

echo [3/5] Installing numpy ...
python -m pip install numpy --quiet
if errorlevel 1 ( echo [ERROR] numpy install failed. & pause & exit /b 1 )

echo [4/5] Installing ezdxf ...
python -m pip install ezdxf --quiet
if errorlevel 1 ( echo [ERROR] ezdxf install failed. & pause & exit /b 1 )

echo [5/5] Installing cadquery-ocp  (large download ~1 GB, may take 5-15 min) ...
python -m pip install cadquery-ocp
if errorlevel 1 (
    echo.
    echo [ERROR] cadquery-ocp install failed.
    echo.
    echo   This package is ~1 GB and requires Python 3.10 or 3.11.
    echo   Make sure you have:
    echo     - Stable internet connection
    echo     - At least 3 GB free disk space
    echo     - Python 3.10 or 3.11  (NOT 3.12+)
    echo.
    pause
    exit /b 1
)

REM ── Verify imports ────────────────────────────────────────────────────────────
echo.
echo Verifying imports ...
python -c "from PyQt6.QtWidgets import QApplication; print('  PyQt6         OK')"
if errorlevel 1 ( echo [ERROR] PyQt6 import failed. & pause & exit /b 1 )

python -c "import numpy; print('  numpy         OK')"
if errorlevel 1 ( echo [ERROR] numpy import failed. & pause & exit /b 1 )

python -c "import ezdxf; print('  ezdxf         OK')"
if errorlevel 1 ( echo [ERROR] ezdxf import failed. & pause & exit /b 1 )

python -c "from OCP.gp import gp_Pnt; print('  cadquery-ocp  OK')"
if errorlevel 1 (
    echo.
    echo [ERROR] OCP import failed even though cadquery-ocp installed.
    echo.
    echo   Try running:
    echo     python -c "from OCP.gp import gp_Pnt"
    echo   and paste the error here.
    echo.
    pause
    exit /b 1
)

echo.
echo ================================================================
echo   All dependencies installed successfully.
echo   Run MTAP with:  python main.py
echo ================================================================
echo.
pause
