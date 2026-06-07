"""
Theme manager — applies the dark or light stylesheet app-wide, injects the
runtime arrow-icon paths, and remembers the choice between sessions.
"""
import os

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from app.utils.config import resource_path
from app.ui.icons_gen import ensure_arrow_icons

_QSS_FILES = {"dark": "styles_dark.qss", "light": "styles_light.qss"}
_current = "light"


def current_theme() -> str:
    return _current


def load_saved() -> str:
    name = QSettings("NTS", "MTAP").value("theme", "light")
    return name if name in _QSS_FILES else "light"


def _save(name: str) -> None:
    QSettings("NTS", "MTAP").setValue("theme", name)


def apply_theme(name: str) -> str:
    """Load the named stylesheet, inject arrow-icon paths, apply it, and persist."""
    global _current
    if name not in _QSS_FILES:
        name = "dark"
    _current = name

    icons = ensure_arrow_icons()
    path = resource_path(os.path.join("app", "ui", _QSS_FILES[name]))
    with open(path, "r", encoding="utf-8") as f:
        qss = f.read()
    # Order matters: replace the longer *_DIM tokens before the short ones.
    for key in ("up_dim", "down_dim", "up", "down"):
        qss = qss.replace(f"__ARROW_{key.upper()}__", icons[key])

    QApplication.instance().setStyleSheet(qss)
    _save(name)
    return name


def toggle() -> str:
    """Switch dark <-> light and return the new theme name."""
    return apply_theme("light" if _current == "dark" else "dark")
