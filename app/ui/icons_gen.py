"""
Generate small arrow icons (for spin boxes / combo boxes) at runtime.

Qt QSS border-triangle arrows render inconsistently, so we draw crisp PNG
triangles instead and reference them from the stylesheet. Files are written to a
writable cache dir and their absolute paths are injected into the QSS (Qt QSS
url() needs real file paths, which differ between dev and a frozen exe).
"""
import os
import tempfile

from PyQt6.QtGui import QPixmap, QPainter, QColor, QPolygonF, QBrush
from PyQt6.QtCore import Qt, QPointF

AMBER = "#c8a800"
DIM = "#3a3a3a"

_W, _H = 18, 12  # master size; QSS scales down for crisp edges


def _arrow(direction: str, color: str) -> QPixmap:
    pm = QPixmap(_W, _H)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(color)))
    pad = 3.0
    if direction == "up":
        pts = [QPointF(pad, _H - pad), QPointF(_W - pad, _H - pad), QPointF(_W / 2, pad)]
    else:  # down
        pts = [QPointF(pad, pad), QPointF(_W - pad, pad), QPointF(_W / 2, _H - pad)]
    p.drawPolygon(QPolygonF(pts))
    p.end()
    return pm


def ensure_arrow_icons() -> dict:
    """Generate the arrow PNGs (idempotent) and return their absolute paths
    with forward slashes (QSS-friendly)."""
    cache = os.path.join(tempfile.gettempdir(), "mtap_icons")
    os.makedirs(cache, exist_ok=True)

    spec = {
        "up": ("up", AMBER),
        "down": ("down", AMBER),
        "up_dim": ("up", DIM),
        "down_dim": ("down", DIM),
    }
    paths = {}
    for key, (direction, color) in spec.items():
        path = os.path.join(cache, f"arrow_{key}.png")
        _arrow(direction, color).save(path, "PNG")
        paths[key] = path.replace("\\", "/")
    return paths
