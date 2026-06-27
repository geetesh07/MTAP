# -*- mode: python ; coding: utf-8 -*-
#
# ONEDIR build — exe is a small launcher; all native DLLs/libs go in _internal/.
# Benefits vs. the old ONEFILE:
#   * Startup time: ~1-2 s instead of 8-10 s (no temp-dir extraction every launch)
#   * OCC crash no longer corrupts a shared temp dir (no temp dir at all)
#   * UPX compresses native DLLs to ~50% so total folder ~120 MB vs 222 MB
#   * The exe itself is ~3 MB — easy to distribute or sign individually
#
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
    # 3D viewer (drill_preview_3d.py) — PyInstaller misses these because
    # the module is imported lazily at runtime inside _build_viewer_panel()
    'PyQt6.QtOpenGLWidgets',
    'PyQt6.QtOpenGL',
    # STEP export — OCP modules imported lazily inside generate_step()
    'OCP.STEPControl', 'OCP.Interface', 'OCP.IFSelect',
    # HLR projection — imported lazily
    'OCP.HLRBRep', 'OCP.HLRAlgo', 'OCP.BRepAdaptor', 'OCP.TopTools',
    'OCP.GCPnts', 'OCP.GeomAbs',
]
# Pull in the whole ezdxf package (dimension renderer, fonts, standards are
# imported dynamically and would be missed by static analysis otherwise).
hiddenimports += collect_submodules('ezdxf')

# Explicit excludes — these are pulled in transitively but not used by MTAP.
# Each adds tens of MB; cut them to keep the folder lean.
excludes = [
    # cadquery high-level API (we only use the OCP C-extension directly)
    'cadquery',
    # Visualisation / data-science stacks — not used
    'matplotlib', 'PIL', 'Pillow',
    'scipy', 'pandas', 'pyarrow',
    'vtk',
    # PDF library — not used
    'pymupdf', 'fitz',
    # Other heavyweight transitive deps
    'IPython', 'notebook', 'jupyter',
    'sklearn', 'cv2', 'tensorflow', 'torch',
    'wx', 'tkinter',
]


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

# ONEDIR: EXE only holds the bootloader + PYZ archive (tiny).
# All binaries and data files go into the COLLECT folder (_internal/).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # keep DLLs out of the exe → COLLECT puts them in _internal/
    name='MTAP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\icons\\mtap.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    # These DLLs cannot be safely compressed — skip them.
    upx_exclude=[
        'vcruntime140.dll', 'vcruntime140_1.dll',
        'msvcp140.dll',
        'python311.dll', 'python3.dll',
        'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll',
    ],
    name='.',   # '.' resolves to distpath itself → MTAP.exe lands directly in dist\
)

# NOTE: vtk.libs must stay — OCP/__init__.py calls add_dll_directory('vtk.libs')
# at import time and raises FileNotFoundError if the dir is absent.
