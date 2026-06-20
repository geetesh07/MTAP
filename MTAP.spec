# -*- mode: python ; coding: utf-8 -*-
# No ezdxf / matplotlib: the only deliverable is the AutoCAD DMTAP link.
datas = [
    ('app\\ui\\styles_dark.qss',  'app\\ui'),
    ('app\\ui\\styles_light.qss', 'app\\ui'),
    ('assets',  'assets'),
]
binaries = []
hiddenimports = ['numpy', 'numpy.core', 'numpy.core._multiarray_umath']

# Exclude heavy unused libs so they can't sneak into the bundle.
excludes = ['ezdxf', 'matplotlib', 'cadquery', 'OCP', 'PIL']


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
    name='MTAP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\icons\\mtap.ico'],
)
