"""
MTAP logo mark — drawn programmatically so it scales crisply and needs no asset
files. The mark is a sharp industrial square framing a left-pointing tool tip
(echoing the drill point in the actual drawings).
"""
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QPolygonF, QBrush
from PyQt6.QtCore import Qt, QPointF

AMBER = "#c8a800"


def make_logo_pixmap(size: int = 34) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    amber = QColor(AMBER)

    # Square frame
    pen = QPen(amber)
    pen.setWidth(max(2, size // 16))
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    inset = pen.width()
    p.drawRect(inset, inset, size - 2 * inset, size - 2 * inset)

    # Left-pointing tool tip (filled triangle) — the drill point
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(amber))
    m = size * 0.30
    tip = QPolygonF([
        QPointF(m, size / 2.0),            # point (left)
        QPointF(size - m, size * 0.30),    # top-right
        QPointF(size - m, size * 0.70),    # bottom-right
    ])
    p.drawPolygon(tip)

    p.end()
    return pm
