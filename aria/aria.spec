# aria.spec - PyInstaller build spec for ARIA
#
# Build with:  pyinstaller aria.spec
#
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
ROOT = Path(SPEC).parent

a = Analysis(
    ['main.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        ('plugins', 'plugins'),
    ] + collect_data_files('customtkinter'),
    hiddenimports=[
        # Text-to-speech (pyttsx3 loads its SAPI5 driver dynamically on Windows)
        'pyttsx3',
        'pyttsx3.drivers',
        'pyttsx3.drivers.sapi5',
        'pyttsx3.drivers.dummy',
        'comtypes',
        'comtypes.client',
        'comtypes.stream',
        'win32com',
        'win32com.client',
        # GUI / core
        'customtkinter',
        'PIL._tkinter_finder',
        'anthropic',
        'openai',
        'pyautogui',
        'pynput',
        'pynput.keyboard',
        'pynput.mouse',
        'pystray',
        'pystray._win32',
        'schedule',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ARIA',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if (ROOT / 'assets' / 'icon.ico').exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ARIA',
)
