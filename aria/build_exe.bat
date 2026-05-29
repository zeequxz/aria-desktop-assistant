@echo off
title ARIA — Build EXE

:: Run from this script's own folder so relative paths resolve correctly.
cd /d "%~dp0"

echo.
echo  ==========================================
echo   ARIA — Build Windows Executable
echo  ==========================================
echo.

:: Install ARIA's dependencies (customtkinter, etc.) so the build can find them.
echo  [*] Installing dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo.
    echo  [!] Failed to install dependencies from requirements.txt.
    pause
    exit /b 1
)

:: Check PyInstaller
pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo  [*] Installing PyInstaller...
    pip install pyinstaller --quiet
)

:: Close any running ARIA so PyInstaller can overwrite dist\ARIA. A running
:: ARIA.exe locks its files, making the build fail with "Access is denied".
echo  [*] Closing ARIA if it is running...
taskkill /IM ARIA.exe /F >nul 2>&1
:: Give Windows a moment to release the file handles.
ping -n 2 127.0.0.1 >nul

echo  [*] Building ARIA.exe...
echo      This takes 2-5 minutes. Please wait.
echo.

pyinstaller aria.spec --noconfirm --clean

if errorlevel 1 (
    echo.
    echo  [!] Build failed. Check the output above for errors.
    pause
    exit /b 1
)

echo.
echo  ==========================================
echo   Build successful!
echo  ==========================================
echo.
echo   Your executable is in:
echo   dist\ARIA\ARIA.exe
echo.
echo   To distribute ARIA, share the entire
echo   dist\ARIA\ folder (not just ARIA.exe).
echo.
echo   Users double-click ARIA.exe to launch.
echo   No Python installation required!
echo.
pause
