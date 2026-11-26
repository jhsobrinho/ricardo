# -*- mode: python ; coding: utf-8 -*-
# Build:  pyinstaller extratos.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, copy_metadata

datas, binaries, hiddenimports = collect_all("streamlit")
datas += copy_metadata("streamlit")
for pkg in ("altair", "pandas", "numpy", "pyarrow", "tornado", "pydeck"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass
datas += [("dashboard.py", "."), ("alvos.py", "."), ("extratos.db", "."),
          (".streamlit", ".streamlit")]

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AnaliseExtratos",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
