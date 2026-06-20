"""Proposal Drawing screen."""

import os
import traceback

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QFileDialog, QMessageBox, QScrollArea, QGroupBox, QGridLayout,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from app.engine.tools.drill import DrillProposalParams
from app.dxf.proposal_dxf import generate as generate_dxf
from app.ui.widgets import NoScrollDoubleSpinBox, NoScrollComboBox
from app.utils.logging_setup import get_logger

log = get_logger()

_DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "MTAP", "proposals")


class ProposalScreen(QWidget):
    back_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._build_ui()

    # ═══════════════════════════════════════════════════════════════ UI build ══

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        form = QWidget()
        page = QVBoxLayout(form)
        page.setContentsMargins(48, 28, 48, 32)
        page.setSpacing(20)

        hdr = QLabel("PARAMETERS")
        hdr.setObjectName("SectionHeader")
        page.addWidget(hdr)

        cols = QHBoxLayout()
        cols.setSpacing(36)

        left = QVBoxLayout(); left.setSpacing(20)
        right = QVBoxLayout(); right.setSpacing(20)

        left.addWidget(self._group_dimensions())
        left.addWidget(self._group_geometry())
        left.addStretch()

        right.addWidget(self._group_flute())
        right.addWidget(self._group_info())
        right.addStretch()

        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        page.addLayout(cols)
        page.addWidget(self._build_action_bar())

        scroll.setWidget(form)
        root.addWidget(scroll)

    # ─── header ───────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setObjectName("TopBar")

        lo = QHBoxLayout(bar)
        lo.setContentsMargins(24, 0, 24, 0)
        lo.setSpacing(16)

        back = QPushButton("← BACK")
        back.setObjectName("BackButton")
        back.setFixedWidth(100)
        back.clicked.connect(self.back_requested)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)

        title = QLabel("PROPOSAL DRAWING")
        title.setObjectName("ScreenTitle")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))

        lo.addWidget(back)
        lo.addWidget(div)
        lo.addWidget(title)
        lo.addStretch()
        return bar

    # ─── groups ───────────────────────────────────────────────────────────────

    def _group_dimensions(self) -> QGroupBox:
        g = QGroupBox("DIMENSIONS")
        grid = QGridLayout(g)
        grid.setColumnStretch(1, 1)
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(16)

        self._dc  = self._spin(0.1, 999.0,  10.0, 3, " mm")
        self._d   = self._spin(0.1, 999.0,  10.0, 3, " mm")
        self._oal = self._spin(1.0, 9999.0, 100.0, 2, " mm")
        self._ls  = self._spin(1.0, 9999.0,  35.0, 2, " mm")

        for r, (lbl, w) in enumerate([
            ("Cutting Ø  (Dc)",       self._dc),
            ("Shank Ø  (D)",          self._d),
            ("Overall Length  (OAL)", self._oal),
            ("Shank Length  (Ls)",    self._ls),
        ]):
            grid.addWidget(QLabel(lbl), r, 0)
            grid.addWidget(w, r, 1)
        return g

    def _group_geometry(self) -> QGroupBox:
        g = QGroupBox("GEOMETRY")
        grid = QGridLayout(g)
        grid.setColumnStretch(1, 1)
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(16)

        self._pa = self._spin(60.0, 160.0, 118.0, 1, "°")
        grid.addWidget(QLabel("Point Angle"), 0, 0)
        grid.addWidget(self._pa, 0, 1)
        return g

    def _group_flute(self) -> QGroupBox:
        g = QGroupBox("FLUTE")
        grid = QGridLayout(g)
        grid.setColumnStretch(1, 1)
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(16)

        self._helix = self._spin(5.0, 85.0, 30.0, 1, "°")

        self._nflutes = NoScrollComboBox()
        for n in ("2 flutes", "3 flutes", "4 flutes"):
            self._nflutes.addItem(n)

        grid.addWidget(QLabel("Helix Angle"),      0, 0)
        grid.addWidget(self._helix,                0, 1)
        grid.addWidget(QLabel("Number of Flutes"), 1, 0)
        grid.addWidget(self._nflutes,              1, 1)
        return g

    def _group_info(self) -> QGroupBox:
        g = QGroupBox("INFO")
        lo = QVBoxLayout(g)

        lbl = QLabel(
            "Generates a proposal DXF using pre-drawn\n"
            "helix blocks scaled to the tool dimensions.\n\n"
            "Implementation in progress."
        )
        lbl.setObjectName("PlaceholderSubText")
        lbl.setWordWrap(True)
        lo.addWidget(lbl)
        return g

    # ─── action bar ───────────────────────────────────────────────────────────

    def _build_action_bar(self) -> QWidget:
        bar = QWidget()
        lo = QHBoxLayout(bar)
        lo.setContentsMargins(0, 12, 0, 0)
        lo.setSpacing(16)

        self._status = QLabel("")
        self._status.setObjectName("PlaceholderSubText")
        self._status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._status.setWordWrap(True)

        btn = QPushButton("GENERATE DXF")
        btn.setObjectName("PrimaryButton")
        btn.setFixedWidth(180)
        btn.setFixedHeight(40)
        btn.clicked.connect(self._on_generate)

        lo.addWidget(self._status)
        lo.addWidget(btn)
        return bar

    # ─── logic ────────────────────────────────────────────────────────────────

    def _spin(self, lo, hi, val, dec, suffix="") -> NoScrollDoubleSpinBox:
        w = NoScrollDoubleSpinBox()
        w.setRange(lo, hi)
        w.setValue(val)
        w.setDecimals(dec)
        if suffix:
            w.setSuffix(suffix)
        return w

    def _read_params(self) -> DrillProposalParams:
        p = DrillProposalParams(
            cutting_diameter = self._dc.value(),
            shank_diameter   = self._d.value(),
            overall_length   = self._oal.value(),
            shank_length     = self._ls.value(),
            point_angle      = self._pa.value(),
            helix_angle      = self._helix.value(),
            n_flutes         = int(self._nflutes.currentText().split()[0]),
        )
        p.derive()
        return p

    def _on_generate(self) -> None:
        try:
            params = self._read_params()
            errs = params.validate()
            if errs:
                self._status.setText("⚠ " + "  ·  ".join(errs))
                return

            Dc  = params.cutting_diameter
            OAL = params.overall_length
            name = f"Drill_Dc{Dc:g}_OAL{OAL:g}.dxf"
            os.makedirs(_DEFAULT_DIR, exist_ok=True)
            default_path = os.path.join(_DEFAULT_DIR, name)

            path, _ = QFileDialog.getSaveFileName(
                self, "Save Proposal DXF", default_path, "DXF Files (*.dxf)"
            )
            if not path:
                return

            self._status.setText("Generating proposal DXF…")
            self.repaint()

            generate_dxf(params, path)

            self._status.setText(f"Saved: {path}")
            log.info("Proposal DXF: %s", path)

            reply = QMessageBox.question(
                self, "Open DXF?",
                f"DXF saved.\n\nOpen {os.path.basename(path)} now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                os.startfile(path)

        except PermissionError as e:
            log.error("Proposal DXF permission error: %s", e)
            self._status.setText("Permission denied — close the file in AutoCAD first.")
            QMessageBox.warning(
                self, "File in Use",
                f"Cannot save — the file is open in another application (AutoCAD).\n\n"
                f"Close the DXF in AutoCAD, then click Generate again.\n\n{e}",
            )
        except Exception:
            tb = traceback.format_exc()
            log.error("Proposal DXF failed:\n%s", tb)
            self._status.setText("Error — see log for details.")
            QMessageBox.critical(self, "Error", tb)
