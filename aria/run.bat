@echo off
title ARIA - Personal AI Assistant
echo.
echo  ==========================================
echo   ARIA - Personal AI Assistant
echo  ==========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [!] Python not found.
    echo      Download from: https://python.org/downloads
    echo      Check "Add Python to PATH" when installing.
    pause
    exit /b
)

if not exist ".dependencies_installed" (
    echo  [*] First-time setup - installing dependencies...
    echo      This takes about 1-2 minutes.
    echo.
    pip install -r requirements.txt --quiet
    echo  [*] Installing Playwright browser...
    playwright install chromium --with-deps
    echo. > .dependencies_installed
    echo  [+] All done!
    echo.
)

echo  [*] Starting ARIA...
echo      You can minimize this window - ARIA will run in the system tray.
echo.
pythonw main.py
