"""
BlankPreview — a native (QPainter only, no matplotlib/ezdxf) 2D sketch of the
drill blank, mirroring what DMTAP draws in AutoCAD: outline, centerline, point /
reinforcement transitions, back-face chamfer, the through-coolant snake, and a
few key dimension labels. Updates live as the form changes.
"""
import math

from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QPen, QPolygonF, QFont
from PyQt6.QtCore import Qt, QPointF

from app.engine.tools.drill import DrillBlankParams, EPS
from app.dxf.lsp_writer import _chamfer_backface

# CAD-like palette on a dark canvas (so the colours read like AutoCAD layers)
C_BG      = QColor("#16140F")
C_GRID    = QColor("#221F18")
C_OUTLINE = QColor("#E3BD2E")   # yellow — MTAP-OUTLINE
C_FILL    = QColor(227, 189, 46, 22)
C_CENTER  = QColor("#5FC6D6")   # cyan — MTAP-CENTER
C_EDGE    = QColor("#C9A24B")   # internal edges (transition / chamfer / point base)
C_COOLANT = QColor("#E07AB6")   # pink — MTAP-COOLANT
C_TEXT    = QColor("#D8D1C0")
C_DIM     = QColor("#E08A7C")   # dim text


class BlankPreview(QWidget):
    def __init__(self):
        super().__init__()
        self._p: DrillBlankParams | None = None
        self.setMinimumWidth(340)
        self.setMinimumHeight(280)

    def set_params(self, p: DrillBlankParams | None) -> None:
        self._p = p
        self.update()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _ev) -> None:
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.fillRect(self.rect(), C_BG)
        try:
            if self._p is not None:
                self._draw(qp, self._p)
        except Exception:
            pass  # never let a transient bad geometry crash the paint
        finally:
            qp.end()

    def _draw(self, qp: QPainter, p: DrillBlankParams) -> None:
        W, H = self.width(), self.height()
        rc = p.cutting_diameter / 2.0
        rs = p.shank_diameter / 2.0
        rmax = max(rc, rs, 0.5)
        oal = max(p.overall_length, 1.0)

        chamfer = 0.1 * p.shank_diameter
        prof = _chamfer_backface(p.profile_points(), p.x_end, rs, chamfer)
        if not prof:
            return

        # model bounds (+ a little vertical headroom for labels)
        minx, maxx = 0.0, oal
        miny, maxy = -rmax, rmax
        mw = max(maxx - minx, 1e-6)
        mh = max(maxy - miny, 1e-6)

        pad_x, pad_top, pad_bot = 40, 38, 52   # screen px; room for labels/dims
        avail_w = max(W - 2 * pad_x, 10)
        avail_h = max(H - pad_top - pad_bot, 10)
        scale = min(avail_w / mw, avail_h / mh)

        cxm, cym = (minx + maxx) / 2.0, (miny + maxy) / 2.0
        ox = W / 2.0
        oy = pad_top + avail_h / 2.0

        def T(mx, my):
            return QPointF(ox + (mx - cxm) * scale, oy - (my - cym) * scale)

        # ---- outline (filled + stroked) ----
        poly = QPolygonF([T(x, y) for x, y in prof])
        qp.setBrush(C_FILL)
        qp.setPen(QPen(C_OUTLINE, 2))
        qp.drawPolygon(poly)

        # ---- centerline (dash-dot) ----
        nub = oal * 0.05
        pen = QPen(C_CENTER, 1)
        pen.setDashPattern([12, 4, 2, 4])
        qp.setPen(pen)
        qp.drawLine(T(-nub, 0), T(oal + nub, 0))

        # ---- internal edges ----
        qp.setPen(QPen(C_EDGE, 1))
        if p.point_length > EPS:                       # point base line
            qp.drawLine(T(p.x_point_base, rc), T(p.x_point_base, -rc))
        reinf = p.reinforcement_length > EPS
        qp.drawLine(T(p.x_body_end, rc), T(p.x_body_end, -rc))   # body end
        if reinf:
            qp.drawLine(T(p.x_shank_start, rs), T(p.x_shank_start, -rs))
        if chamfer > EPS:                              # back-face chamfer root
            qp.drawLine(T(p.x_end - chamfer, rs), T(p.x_end - chamfer, -rs))

        # ---- through-coolant snake (pink, dashed), mirrored ----
        if getattr(p, "coolant", False):
            amp = min(rc, rs) * 0.5
            cx0 = p.x_point_base + 0.70 * p.flute_length
            cx1 = p.x_end
            span = max(cx1 - cx0, EPS)
            waves = max(1.0, min(span / (p.cutting_diameter * 8.0), 4.0))
            wl = span / waves
            n = 64
            up, dn = [], []
            for i in range(n + 1):
                x = cx0 + span * i / n
                y = amp * math.sin(2.0 * math.pi * (x - cx0) / wl)
                up.append(T(x, y)); dn.append(T(x, -y))
            cpen = QPen(C_COOLANT, 1)
            cpen.setDashPattern([6, 4])
            qp.setPen(cpen)
            qp.drawPolyline(QPolygonF(up))
            qp.drawPolyline(QPolygonF(dn))

        # ---- key dimension labels ----
        qp.setFont(QFont("Segoe UI", 8))
        def label(text, mx, my, color=C_TEXT, dy=0, dx=0, align=Qt.AlignmentFlag.AlignCenter):
            pt = T(mx, my)
            qp.setPen(color)
            r = qp.fontMetrics().boundingRect(text)
            qp.drawText(int(pt.x() - r.width() / 2 + dx), int(pt.y() + dy), text)

        label(f"Ø{p.cutting_diameter:g}", p.x_point_base if p.point_length > EPS else 0.0, rc, C_DIM, dy=-8)
        label(f"Ø{p.shank_diameter:g}", p.x_end, rs, C_DIM, dy=-8)
        label(f"OAL {p.overall_length:g}", oal / 2.0, -rmax, C_DIM, dy=34)
        if p.point_length > EPS:
            label(f"{p.point_angle:g}°", 0.0, 0.0, C_TEXT, dx=int(scale * oal * 0.06) + 14)
