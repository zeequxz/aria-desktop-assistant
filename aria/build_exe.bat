@echo off
setlocal enabledelayedexpansion
title ARIA - Build EXE

:: Run from this script's own folder so relative paths resolve correctly.
cd /d "%~dp0"

echo.
echo  ==========================================
echo   ARIA - Build Windows Executable
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

:: Try to clear the old dist\ARIA. Right after a build, Windows Defender (or an
:: open Explorer window) can briefly lock this folder, which makes PyInstaller's
:: COLLECT step fail. Retry a few times; if it stays locked, build into a fresh
:: folder instead so the build still succeeds.
set "OUTDIR=dist"
if exist "dist\ARIA" (
    echo  [*] Clearing old dist\ARIA...
    set /a tries=0
    :rmloop
    rmdir /s /q "dist\ARIA" >nul 2>&1
    if not exist "dist\ARIA" goto rmdone
    set /a tries+=1
    if !tries! geq 5 goto rmlocked
    echo      still locked, retrying (!tries!/5)...
    ping -n 4 127.0.0.1 >nul
    goto rmloop
    :rmlocked
    echo  [!] dist\ARIA is locked (close any Explorer window open on it).
    echo      Building into dist_build\ instead.
    set "OUTDIR=dist_build"
    rmdir /s /q "dist_build" >nul 2>&1
    :rmdone
)

echo  [*] Building ARIA.exe...
echo      This takes 2-5 minutes. Please wait.
echo.

pyinstaller aria.spec --noconfirm --clean --distpath "%OUTDIR%"

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
echo   %OUTDIR%\ARIA\ARIA.exe
echo.
echo   To distribute ARIA, share the entire
echo   %OUTDIR%\ARIA\ folder (not just ARIA.exe).
echo.
echo   Users double-click ARIA.exe to launch.
echo   No Python installation required!
echo.
pause
