import os
import traceback
from datetime import date

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QPushButton, QLineEdit, QPlainTextEdit,
    QScrollArea, QGroupBox, QSizePolicy, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from app.engine.tools.drill import DrillBlankParams
from app.dxf.lsp_writer import LspWriter
from app.ui.widgets import YesNoToggle, NoScrollDoubleSpinBox, NoScrollComboBox
from app.utils.config import AUTOCAD_LINK_DIR, AUTOCAD_LINK_PATH
from app.utils.logging_setup import get_logger

log = get_logger()


class BlankDrawingScreen(QWidget):
    back_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._params: DrillBlankParams | None = None
        self._valid = False
        self._build_ui()
        self._recompute()

    # ===================================================================== UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        # Scrollable form (no preview panel any more)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Full-width two-column layout of section cards
        form = QWidget()
        page = QVBoxLayout(form)
        page.setContentsMargins(48, 26, 48, 26)
        page.setSpacing(18)

        header = QLabel("PARAMETERS")
        header.setObjectName("SectionHeader")
        page.addWidget(header)

        cols = QHBoxLayout()
        cols.setSpacing(32)

        left = QVBoxLayout(); left.setSpacing(20)
        right = QVBoxLayout(); right.setSpacing(20)

        left.addWidget(self._group_tool())
        left.addWidget(self._group_dimensions())
        left.addWidget(self._group_geometry())
        left.addWidget(self._group_flute())
        left.addStretch()

        right.addWidget(self._group_annotations())
        right.addWidget(self._group_details())
        right.addWidget(self._group_derived())
        right.addStretch()

        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        page.addLayout(cols)
        page.addStretch()

        scroll.setWidget(form)
        root.addWidget(scroll, stretch=1)

        # Action bar (fixed at bottom)
        root.addWidget(self._build_action_bar())

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setObjectName("TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(16)

        back_btn = QPushButton("← BACK")
        back_btn.setObjectName("BackButton")
        back_btn.setFixedWidth(100)
        back_btn.clicked.connect(self.back_requested)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)

        title = QLabel("BLANK DRAWING")
        title.setObjectName("ScreenTitle")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))

        subtitle = QLabel("Twist drill · raw stock before fluting")
        subtitle.setObjectName("ScreenSubtitle")

        layout.addWidget(back_btn)
        layout.addWidget(divider)
        layout.addWidget(title)
        layout.addSpacing(8)
        layout.addWidget(subtitle)
        layout.addStretch()
        return bar

    # ----------------------------------------------------------------- widgets
    def _spin(self, value, lo, hi, decimals=3, step=1.0) -> NoScrollDoubleSpinBox:
        sb = NoScrollDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setDecimals(decimals)
        sb.setSingleStep(step)
        sb.setValue(value)
        sb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sb.valueChanged.connect(self._on_input_change)
        return sb

    def _field(self, label: str, widget: QWidget, unit: str = "") -> QWidget:
        """A modern field: small label stacked above the input (with optional unit)."""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        lab = QLabel(label)
        lab.setObjectName("FieldLabel")
        v.addWidget(lab)
        if unit:
            r = QWidget()
            h = QHBoxLayout(r)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            h.addWidget(widget, 1)
            u = QLabel(unit)
            u.setObjectName("FieldUnit")
            h.addWidget(u)
            v.addWidget(r)
        else:
            v.addWidget(widget)
        return box

    def _card(self, title: str) -> tuple[QGroupBox, QGridLayout]:
        box = QGroupBox(title)
        grid = QGridLayout(box)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(16)
        return box, grid

    def _group_tool(self) -> QGroupBox:
        box, grid = self._card("TOOL")
        self.tool_combo = NoScrollComboBox()
        self.tool_combo.addItem("Drill")
        self.tool_combo.addItem("End Mill (soon)")
        self.tool_combo.addItem("Reamer (soon)")
        for i in (1, 2):
            self.tool_combo.model().item(i).setEnabled(False)
        grid.addWidget(self._field("Tool type", self.tool_combo), 0, 0, 1, 2)
        return box

    def _group_dimensions(self) -> QGroupBox:
        box, grid = self._card("DIMENSIONS")
        self.dc_spin = self._spin(10.0, 0.001, 100000, step=0.5)
        self.d_spin = self._spin(10.0, 0.001, 100000, step=0.5)
        self.oal_spin = self._spin(100.0, 0.001, 100000, step=1.0)
        self.ls_spin = self._spin(40.0, 0.001, 100000, step=1.0)
        grid.addWidget(self._field("Cutting dia (Dc)", self.dc_spin, "mm"), 0, 0)
        grid.addWidget(self._field("Shank dia (D)", self.d_spin, "mm"), 0, 1)
        grid.addWidget(self._field("Overall length (OAL)", self.oal_spin, "mm"), 1, 0)
        grid.addWidget(self._field("Shank length (Ls)", self.ls_spin, "mm"), 1, 1)
        return box

    def _group_geometry(self) -> QGroupBox:
        box, grid = self._card("GEOMETRY")
        self.point_spin = self._spin(140.0, 1.0, 180.0, decimals=1, step=1.0)
        grid.addWidget(self._field("Point angle", self.point_spin, "°"), 0, 0)

        self.reinf_toggle = YesNoToggle(value=False)
        self.reinf_toggle.changed.connect(self._on_reinf_toggle)
        grid.addWidget(self._field("Shank reinforcement", self.reinf_toggle), 0, 1)

        self.reinf_angle_spin = self._spin(30.0, 0.1, 89.9, decimals=1, step=1.0)
        self.reinf_angle_field = self._field("Reinf angle (from CL)", self.reinf_angle_spin, "°")
        self.reinf_angle_field.setVisible(False)
        grid.addWidget(self.reinf_angle_field, 1, 0)

        self.coolant_toggle = YesNoToggle(value=False)
        grid.addWidget(self._field("Through coolant", self.coolant_toggle), 1, 1)
        return box

    def _group_annotations(self) -> QGroupBox:
        box, grid = self._card("ANNOTATIONS")
        self.back_taper_spin = self._spin(0.050, 0.0, 10.0, decimals=3, step=0.001)
        grid.addWidget(self._field("Back taper", self.back_taper_spin, "mm/100"), 0, 0)
        self.runout_spin = self._spin(0.010, 0.0, 1.0, decimals=3, step=0.001)
        grid.addWidget(self._field("Runout tol (GD&T)", self.runout_spin, "mm"), 0, 1)
        return box

    def _group_flute(self) -> QGroupBox:
        box, grid = self._card("FLUTE")
        self.flute_auto = YesNoToggle(value=True)
        self.flute_auto.changed.connect(self._on_flute_auto_toggle)
        grid.addWidget(self._field("Auto flute length", self.flute_auto), 0, 0)
        self.flute_spin = self._spin(60.0, 0.0, 100000, step=1.0)
        self.flute_spin.setEnabled(False)
        grid.addWidget(self._field("Flute length", self.flute_spin, "mm"), 0, 1)
        return box

    def _group_details(self) -> QGroupBox:
        """Title-block / template details — filled into the customer template."""
        box, grid = self._card("DRAWING DETAILS")
        self.customer_edit = QLineEdit()
        self.customer_edit.setPlaceholderText("Customer name")
        grid.addWidget(self._field("Customer", self.customer_edit), 0, 0)

        self.drawn_edit = QLineEdit()
        self.drawn_edit.setPlaceholderText("Drawn by")
        grid.addWidget(self._field("Drawn by", self.drawn_edit), 0, 1)

        self.checked_edit = QLineEdit()
        self.checked_edit.setPlaceholderText("Checked by")
        grid.addWidget(self._field("Checked by", self.checked_edit), 1, 0)

        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText("Longer description for the title block…")
        self.desc_edit.setFixedHeight(84)
        grid.addWidget(self._field("Description", self.desc_edit), 2, 0, 1, 2)
        return box

    def _group_derived(self) -> QGroupBox:
        box, grid = self._card("DERIVED")
        self.read_point = self._readout(grid, 0, 0, "Point length")
        self.read_reinf = self._readout(grid, 0, 1, "Reinforcement length")
        self.read_body = self._readout(grid, 1, 0, "Body length")
        self.read_flute = self._readout(grid, 1, 1, "Flute length")

        self.status_label = QLabel("")
        self.status_label.setObjectName("StatusOk")
        self.status_label.setWordWrap(True)
        grid.addWidget(self.status_label, 2, 0, 1, 2)
        return box

    def _readout(self, grid: QGridLayout, r: int, c: int, key: str) -> QLabel:
        cell = QWidget()
        v = QVBoxLayout(cell)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        k = QLabel(key)
        k.setObjectName("ReadoutKey")
        val = QLabel("—")
        val.setObjectName("ReadoutValue")
        v.addWidget(k)
        v.addWidget(val)
        grid.addWidget(cell, r, c)
        return val

    def _build_action_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("LeftPanel")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(28, 12, 28, 18)
        layout.setSpacing(10)

        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        # AutoCAD live link: writes the canonical link file that DMTAP reads.
        self.export_link_btn = QPushButton("AUTOCAD LINK")
        self.export_link_btn.setObjectName("PrimaryButton")
        self.export_link_btn.setFixedHeight(44)
        self.export_link_btn.clicked.connect(self._export_link)
        layout.addWidget(self.export_link_btn)

        self.caption = QLabel("")
        self.caption.setObjectName("PreviewCaption")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.caption)
        return bar

    # ================================================================== logic
    def _collect_params(self) -> DrillBlankParams:
        flute_override = None if self.flute_auto.value() else self.flute_spin.value()
        return DrillBlankParams(
            tool_type="Drill",
            cutting_diameter=self.dc_spin.value(),
            shank_diameter=self.d_spin.value(),
            overall_length=self.oal_spin.value(),
            shank_length=self.ls_spin.value(),
            reinforcement=self.reinf_toggle.value(),
            reinforcement_angle=self.reinf_angle_spin.value(),
            point_angle=self.point_spin.value(),
            flute_length_override=flute_override,
            back_taper=self.back_taper_spin.value(),
            runout=self.runout_spin.value(),
            coolant=self.coolant_toggle.value(),
        )

    def _collect_meta(self) -> dict:
        return {
            "customer": self.customer_edit.text().strip(),
            "drawn_by": self.drawn_edit.text().strip(),
            "checked_by": self.checked_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "date": date.today().strftime("%d/%m/%y"),
        }

    def _on_input_change(self) -> None:
        self._recompute()

    def _on_reinf_toggle(self, checked: bool) -> None:
        self.reinf_angle_field.setVisible(checked)
        self._recompute()

    def _on_flute_auto_toggle(self, checked: bool) -> None:
        self.flute_spin.setEnabled(not checked)
        self._recompute()

    def _recompute(self) -> None:
        p = self._collect_params()
        p.derive()
        errors = p.validate()
        self._params = p

        self.read_point.setText("FLAT" if p.point_length < 1e-6 else f"{p.point_length:.3f} mm")
        self.read_reinf.setText(f"{p.reinforcement_length:.3f} mm")
        self.read_body.setText(f"{max(p.body_length, 0):.3f} mm")
        self.read_flute.setText(f"{p.flute_length:.3f} mm")

        self.flute_spin.blockSignals(True)
        self.flute_spin.setMaximum(max(p.body_length, 0.0) if p.body_length > 0 else 100000)
        if self.flute_auto.value():
            self.flute_spin.setValue(max(p.body_length, 0.0))
        self.flute_spin.blockSignals(False)

        if errors:
            self._valid = False
            self.status_label.setObjectName("StatusError")
            self.status_label.setText("✕ " + errors[0])
            self._set_exports_enabled(False)
        else:
            self._valid = True
            self.status_label.setObjectName("StatusOk")
            self.status_label.setText("✓ Geometry valid")
            self._set_exports_enabled(True)

        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _set_exports_enabled(self, enabled: bool) -> None:
        self.export_link_btn.setEnabled(enabled)

    # ---------------------------------------------------------------- export
    def _export_link(self) -> None:
        """
        Write the AutoCAD live link: a self-contained AutoLISP file at the
        canonical path. The user then types DMTAP in AutoCAD (no file dialog)
        and it draws this exact tool at 1:1 scale, with the entered details.
        """
        if not self._valid or self._params is None:
            return
        try:
            os.makedirs(AUTOCAD_LINK_DIR, exist_ok=True)
            meta = self._collect_meta()
            log.info("Writing AutoCAD link -> %s", AUTOCAD_LINK_PATH)
            LspWriter(self._params, meta).generate(AUTOCAD_LINK_PATH)
            ok = (os.path.exists(AUTOCAD_LINK_PATH)
                  and os.path.getsize(AUTOCAD_LINK_PATH) > 0)
            log.info("AutoCAD link %s (size=%s bytes)",
                     "OK" if ok else "FAILED",
                     os.path.getsize(AUTOCAD_LINK_PATH) if ok else 0)
            self.caption.setText("AutoCAD link ready → type DMTAP in AutoCAD")
            QMessageBox.information(
                self, "MTAP by NTS — AutoCAD Link",
                "The link is ready.\n\n"
                "Switch to AutoCAD and type:  DMTAP\n\n"
                "It will draw this tool at 1:1 scale.\n\n"
                "(First time only: load autocad\\mtap.lsp via APPLOAD, or add it "
                "to the AutoCAD Startup Suite so DMTAP is always available.)",
            )
        except Exception as e:
            self._report_error("AutoCAD link failed", e)

    def _report_error(self, title: str, exc: Exception) -> None:
        log.exception(title)
        QMessageBox.critical(
            self, f"MTAP by NTS — {title}",
            f"{type(exc).__name__}: {exc}\n\n"
            f"{traceback.format_exc()}\n"
            f"This has also been written to logs/mtap.log",
        )
