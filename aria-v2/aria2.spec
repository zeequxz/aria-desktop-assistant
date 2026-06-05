# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ARIA v2.

Build a windowed Windows executable:

    pip install pyinstaller
    python -m PyInstaller --noconfirm aria2.spec    (or: build.bat)

Output: dist\ARIA2\ARIA2.exe

IMPORTANT: Always run ARIA2.exe from inside dist\ARIA2\ — do NOT run the
intermediate files in the build\ directory.
"""

import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("aria2/core/schema.sql", "aria2/core"),
    ("aria2/devtools/echo_mcp_server.py", "aria2/devtools"),
    ("aria2/assets", "aria2/assets"),
]
datas += collect_data_files("customtkinter")

hiddenimports = [
    "anthropic", "openai", "requests",
    "win32crypt",
]
hiddenimports += collect_submodules("customtkinter")

# Bundle the Python DLL and the Visual C++ runtime DLLs it depends on so the
# bootloader can find them in _internal/ without needing them on PATH or a
# separate VC++ Redistributable install.
_py_dir = os.path.dirname(sys.executable)
_sys32  = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "System32")
binaries = []
for _dll in ("python312.dll", "python3.dll",
             "MSVCP140.dll",  "MSVCP140_1.dll", "MSVCP140_2.dll",
             "VCRUNTIME140.dll", "VCRUNTIME140_1.dll",
             "CONCRT140.dll", "VCOMP140.dll"):
    for _dir in (_py_dir, _sys32):
        _path = os.path.join(_dir, _dll)
        if os.path.exists(_path):
            binaries.append((_path, "."))
            break

a = Analysis(
    ["aria2/__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter.test", "test", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ARIA2",
    console=False,
    disable_windowed_traceback=False,
    icon="aria2/assets/aria2.ico",
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,   # UPX off: avoids AV false-positives + DLL issues
    name="ARIA2",
)
