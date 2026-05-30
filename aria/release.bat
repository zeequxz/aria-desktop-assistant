@echo off
title ARIA - Release Builder
setlocal enabledelayedexpansion

:: Run from this script's own folder so relative paths resolve correctly.
cd /d "%~dp0"

echo.
echo  ==========================================
echo   ARIA - Build and package a release
echo  ==========================================
echo.

:: ── Read CURRENT_VERSION from agent\updater.py ───────────────────────────────
:: This is the single source of truth for the app version. Bump it there before
:: running this script.
set "VERSION="
for /f "tokens=2 delims==" %%V in ('findstr /b /c:"CURRENT_VERSION" agent\updater.py') do (
    set "RAW=%%V"
)
:: Strip spaces and quotes from the captured value (e.g.  "1.0.1"  -> 1.0.1).
set "RAW=%RAW: =%"
set "RAW=%RAW:"=%"
set "VERSION=%RAW%"

if "%VERSION%"=="" (
    echo  [!] Could not read CURRENT_VERSION from agent\updater.py
    pause
    exit /b 1
)

echo  [*] Version: v%VERSION%
echo.

:: ── Build the executable ─────────────────────────────────────────────────────
call build_exe.bat
if errorlevel 1 (
    echo  [!] Build failed; aborting release.
    exit /b 1
)

:: ── Locate the build. build_exe.bat normally produces dist\ARIA, but falls
:: back to dist_build\ARIA when dist\ARIA is locked. Use whichever exists.
set "BUILT="
if exist "dist\ARIA\ARIA.exe" set "BUILT=dist\ARIA"
if not defined BUILT if exist "dist_build\ARIA\ARIA.exe" set "BUILT=dist_build\ARIA"
if not defined BUILT (
    echo  [!] Could not find a built ARIA.exe in dist\ or dist_build\.
    pause
    exit /b 1
)
echo  [*] Using build at: %BUILT%

if not exist "release" mkdir "release"
set "ZIP=release\ARIA-v%VERSION%.zip"
if exist "%ZIP%" del "%ZIP%"

echo.
echo  [*] Packaging %ZIP% ...
:: Zipping the build's contents keeps ARIA.exe at the root of the zip, which is
:: what the in-app updater expects.
powershell -NoProfile -Command "Compress-Archive -Path '%BUILT%\*' -DestinationPath '%ZIP%' -Force"
if errorlevel 1 (
    echo  [!] Packaging failed.
    pause
    exit /b 1
)

echo.
echo  ==========================================
echo   Release package ready
echo  ==========================================
echo.
echo   File:  %ZIP%
echo.
echo   Next steps to publish:
echo     1. git commit + push your version bump
echo     2. Create a GitHub Release tagged  v%VERSION%
echo     3. Attach  %ZIP%  as a release asset
echo.
echo   Users on older versions get the in-app update prompt.
echo.
pause
