@echo off
title Fetchly — Installer
color 0A

echo.
echo  ========================================
echo    Fetchly — Install
echo  ========================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python is not installed or not in PATH.
    echo.
    echo  Please install Python 3.10 or newer from:
    echo    https://www.python.org/downloads/
    echo.
    echo  Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo  [OK] Python found.
echo.
echo  Installing dependencies...
echo.
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  [ERROR] Dependency installation failed.
    echo  Check your internet connection and try again.
    pause
    exit /b 1
)

echo.
echo  ========================================
echo    Installation complete!
echo    Run Fetchly with: python main.py
echo    Or double-click run.bat
echo  ========================================
echo.
pause
