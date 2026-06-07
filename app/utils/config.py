import os
import sys

APP_NAME = "MTAP"            # internal id (paths, QSettings) — do NOT change
APP_BRAND = "MTAP by NTS"    # user-facing brand shown across the UI
APP_FULL_NAME = "Machine Tool Automation Program"
APP_VERSION = "0.1.0"


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _base_dir() -> str:
    """Folder of the exe (when frozen) or the project root (in development).
    Use this for WRITABLE output/logs."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return _project_root()


def resource_path(relative: str) -> str:
    """Resolve a bundled READ-ONLY resource (qss, icons). When frozen, PyInstaller
    unpacks data to sys._MEIPASS; in dev it's the project root."""
    base = getattr(sys, "_MEIPASS", _project_root())
    return os.path.join(base, relative)


# Absolute output directory, always next to the exe/project (NOT the cwd, which
# may be read-only when the exe is launched from a shortcut).
OUTPUT_DIR = os.path.join(_base_dir(), "output")

# Canonical "AutoCAD link" location. The app writes the current tool's geometry
# here as an AutoLISP data file; the DMTAP command in AutoCAD reads this EXACT
# path automatically (no file picker). Lives under the user profile so both the
# frozen exe and AutoCAD resolve to the same place:
#   Python : os.path.expanduser("~")  ->  %USERPROFILE%
#   LISP   : (getenv "USERPROFILE")
# Keep this in sync with autocad/mtap.lsp (MTAP:link-path).
AUTOCAD_LINK_DIR = os.path.join(os.path.expanduser("~"), "MTAP")
AUTOCAD_LINK_PATH = os.path.join(AUTOCAD_LINK_DIR, "mtap_link.lsp")

# DXF layer names — all geometry lives on named layers for AutoCAD compatibility
LAYER_OUTLINE = "OUTLINE"
LAYER_DIMENSION = "DIMENSION"
LAYER_CENTERLINE = "CENTERLINE"
LAYER_HIDDEN = "HIDDEN"
LAYER_ANNOTATION = "ANNOTATION"
LAYER_TITLE_BLOCK = "TITLE_BLOCK"
LAYER_HATCH = "HATCH"

# DXF units (1 = inches, 4 = mm)
DXF_UNITS_MM = 4

# Drawing scale — 1:1 always; scaling happens via viewport in AutoCAD
DRAWING_SCALE = 1.0
