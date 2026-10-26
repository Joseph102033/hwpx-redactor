@echo off
chcp 65001 >nul
cd /d "%~dp0"
title HWPX_Lite - folder watcher
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found.
  echo  - Install Python 3 from https://www.python.org and check "Add Python to PATH".
  echo  - Then run:  pip install pillow pywin32
  pause
  exit /b
)
python watch_lite.py
echo.
echo [watcher stopped]
pause
