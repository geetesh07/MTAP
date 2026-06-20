# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

datas = [
    ('app\\ui\\styles_dark.qss',  'app\\ui'),
    ('app\\ui\\styles_light.qss', 'app\\ui'),
    ('assets',  'assets'),
    # Proposal Drawing: Node.js projection script + three-edge-projection packages
    ('nodejs', 'nodejs'),
]
# ezdxf with setup=True needs its font/standards data files at runtime.
datas += collect_data_files('ezdxf')

binaries = []
hiddenimports = [
    'numpy', 'numpy.core', 'numpy.core._multiarray_umath',
    # OCP modules used by proposal_dxf
    'OCP.gp', 'OCP.BRepPrimAPI', 'OCP.BRepAlgoAPI',
    'OCP.BRepBuilderAPI', 'OCP.BRepOffsetAPI', 'OCP.BRepMesh',
    'OCP.BRep', 'OCP.TopLoc', 'OCP.GeomAPI', 'OCP.TColgp',
    'OCP.TopExp', 'OCP.TopAbs', 'OCP.TopoDS', 'OCP.GC',
]
# Pull in the whole ezdxf package (dimension renderer, fonts, standards are
# imported dynamically and would be missed by static analysis otherwise).
hiddenimports += collect_submodules('ezdxf')

# cadquery high-level API and matplotlib still excluded (not used).
excludes = ['matplotlib', 'cadquery', 'PIL']


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
