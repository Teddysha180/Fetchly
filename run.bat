@echo off
title Fetchly
python main.py
if errorlevel 1 (
    echo.
    echo  [ERROR] Failed to start Fetchly.
    echo  Run install.bat first if you haven't already.
    pause
)
