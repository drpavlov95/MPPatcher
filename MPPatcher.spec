# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

# scipy's array-API shim reaches its backends by name at import time
# (`scipy._external.array_api_compat.numpy` does `from .fft import *`), so
# PyInstaller's static analysis never sees them. The symptom is a
# ModuleNotFoundError on the user's machine and nothing at all at build time —
# measured 2026-11-06: `No module named
# 'scipy._external.array_api_compat.numpy.fft'`, raised the first time a
# headpart run reached the surface wrap. Collect the shim wholesale; it is small,
# and naming its members one at a time only moves the next failure later.
_HIDDEN = collect_submodules('scipy._external.array_api_compat')


# Headparts (library mode) needs `nifdump.exe` at runtime. It is NOT bundled
# here: it is built by the same CMake project as `mppatcher-cli.exe` and is
# deployed beside this exe, exactly like the CLI, so both native binaries can be
# replaced without rebuilding the Python side. core.nif_geometry resolves it from
# the exe directory, then its parent, then MPP_NIFDUMP.
#
# scipy arrives through fmp.wrap (cKDTree) and is found by analysis — the import
# is inside the wrap call, which PyInstaller still follows.
a = Analysis(
    ['mppatcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MPPatcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Keep the spec portable: add an icon path here only when packaging with
    # an icon that is present in the checkout.
    icon=None,
)
