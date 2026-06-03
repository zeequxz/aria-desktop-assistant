@echo off
REM Build ARIA v2 → dist\ARIA2\ARIA2.exe
cd /d "%~dp0"

echo Installing / upgrading build deps...
python -m pip install --upgrade pyinstaller >nul
python -m pip install -r requirements.txt >nul

echo Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo Building...
python -m PyInstaller --noconfirm --distpath dist --workpath build aria2.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED. See output above.
    pause
    exit /b 1
)

REM ── Delete the build/ directory immediately so no one accidentally runs
REM    the incomplete intermediate exe that lives there.
echo Cleaning intermediate build files...
if exist build rmdir /s /q build

echo.
echo =============================================
echo  Build complete.
echo  Executable: dist\ARIA2\ARIA2.exe
echo =============================================
echo.

REM Open Explorer to the output folder so you can see the exe directly.
explorer "%~dp0dist\ARIA2"
