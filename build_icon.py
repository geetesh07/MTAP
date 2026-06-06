"""
Generate assets/icons/mtap.ico for the application / exe.
Run headless (offscreen). Produces a multi-size .ico from a drawn 256px master.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont, QPen
from PyQt6.QtCore import Qt

OUT = os.path.join("assets", "icons", "mtap.ico")


def make_master(size: int) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(QColor("#1a1a1a"))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # amber border
    pen = QPen(QColor("#c8a800"))
    pen.setWidth(max(2, size // 24))
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    m = size // 12
    p.drawRect(m, m, size - 2 * m, size - 2 * m)

    # "M"
    p.setPen(QColor("#c8a800"))
    f = QFont("Segoe UI", int(size * 0.55), QFont.Weight.Bold)
    p.setFont(f)
    p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "M")
    p.end()
    return px


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841 (needed for QPixmap)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    master = make_master(256)
    if master.save(OUT, "ICO"):
        print(f"Wrote {OUT}")
        return 0
    print("Failed to write ICO", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
