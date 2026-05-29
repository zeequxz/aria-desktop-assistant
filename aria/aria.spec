# aria.spec - PyInstaller build spec for ARIA
#
# Build with:  pyinstaller aria.spec
# Output:      dist/ARIA/ARIA.exe  (folder, share the whole folder)
# Single file: change onedir to onefile below (slower startup)

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
ROOT = Path(SPEC).parent  # Folder containing this .spec file

a = Analysis(
    ['main.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Include the plugins folder
        ('plugins', 'plugins'),
    # Include CustomTkinter themes/assets. collect_data_files locates the
    # package wherever pip installed it (global or user site-packages)
    # instead of guessing a hard-coded path under sys.executable.
    ] + collect_data_files('customtkinter'),
    hiddenimports=[
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
        'psutil',
        'pyperclip',
        'docx',
        'openpyxl',
        'duckduckgo_search',
        'requests',
        'win10toast',
        'playwright',
        'playwright.sync_api',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas', 'tensorflow', 'torch'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
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
    console=False,        # No terminal window shown to users
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='assets/icon.ico',   # Uncomment and add icon.ico file
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
