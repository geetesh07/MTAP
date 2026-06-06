import os
import traceback
from datetime import date

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QPushButton, QComboBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit,
    QScrollArea, QGroupBox, QSizePolicy, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from app.engine.tools.drill import DrillBlankParams
from app.dxf.lsp_writer import LspWriter
from app.ui.widgets import YesNoToggle
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

        form = QWidget()
        f = QVBoxLayout(form)
        f.setContentsMargins(28, 26, 28, 22)
        f.setSpacing(16)

        header = QLabel("PARAMETERS")
        header.setObjectName("SectionHeader")
        f.addWidget(header)

        f.addWidget(self._group_tool())
        f.addWidget(self._group_dimensions())
        f.addWidget(self._group_geometry())
        f.addWidget(self._group_annotations())
        f.addWidget(self._group_flute())
        f.addWidget(self._group_details())
        f.addWidget(self._group_derived())
        f.addStretch()

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
    def _spin(self, value, lo, hi, decimals=3, step=1.0) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setDecimals(decimals)
        sb.setSingleStep(step)
        sb.setValue(value)
        sb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sb.valueChanged.connect(self._on_input_change)
        return sb

    def _row(self, grid: QGridLayout, r: int, label: str, widget: QWidget, unit: str = "") -> None:
        lbl = QLabel(label)
        lbl.setObjectName("FieldLabel")
        grid.addWidget(lbl, r, 0)
        grid.addWidget(widget, r, 1)
        u = QLabel(unit)
        u.setObjectName("FieldUnit")
        u.setFixedWidth(28)
        grid.addWidget(u, r, 2)

    def _group_tool(self) -> QGroupBox:
        box = QGroupBox("TOOL")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.tool_combo = QComboBox()
        self.tool_combo.addItem("Drill")
        self.tool_combo.addItem("End Mill (soon)")
        self.tool_combo.addItem("Reamer (soon)")
        for i in (1, 2):
            self.tool_combo.model().item(i).setEnabled(False)
        self._row(grid, 0, "Tool type", self.tool_combo)
        return box

    def _group_dimensions(self) -> QGroupBox:
        box = QGroupBox("DIMENSIONS")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.dc_spin = self._spin(10.0, 0.001, 100000, step=0.5)
        self.d_spin = self._spin(10.0, 0.001, 100000, step=0.5)
        self.oal_spin = self._spin(100.0, 0.001, 100000, step=1.0)
        self.ls_spin = self._spin(40.0, 0.001, 100000, step=1.0)

        self._row(grid, 0, "Cutting dia (Dc)", self.dc_spin, "mm")
        self._row(grid, 1, "Shank dia (D)", self.d_spin, "mm")
        self._row(grid, 2, "Overall len (OAL)", self.oal_spin, "mm")
        self._row(grid, 3, "Shank len (Ls)", self.ls_spin, "mm")
        return box

    def _group_geometry(self) -> QGroupBox:
        box = QGroupBox("GEOMETRY")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.point_spin = self._spin(140.0, 1.0, 180.0, decimals=1, step=1.0)
        self._row(grid, 0, "Point angle", self.point_spin, "°")

        self.reinf_toggle = YesNoToggle(value=False)
        self.reinf_toggle.changed.connect(self._on_reinf_toggle)
        self._row(grid, 1, "Shank reinforcement", self.reinf_toggle)

        self.reinf_angle_label = QLabel("Reinf angle (from CL)")
        self.reinf_angle_label.setObjectName("FieldLabel")
        self.reinf_angle_spin = self._spin(30.0, 0.1, 89.9, decimals=1, step=1.0)
        self.reinf_angle_unit = QLabel("°")
        self.reinf_angle_unit.setObjectName("FieldUnit")
        self.reinf_angle_unit.setFixedWidth(28)
        grid.addWidget(self.reinf_angle_label, 2, 0)
        grid.addWidget(self.reinf_angle_spin, 2, 1)
        grid.addWidget(self.reinf_angle_unit, 2, 2)
        self.reinf_angle_label.setVisible(False)
        self.reinf_angle_spin.setVisible(False)
        self.reinf_angle_unit.setVisible(False)
        return box

    def _group_annotations(self) -> QGroupBox:
        box = QGroupBox("ANNOTATIONS")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.back_taper_spin = self._spin(0.050, 0.0, 10.0, decimals=3, step=0.001)
        self._row(grid, 0, "Back taper", self.back_taper_spin, "mm/100mm")

        self.runout_spin = self._spin(0.010, 0.0, 1.0, decimals=3, step=0.001)
        self._row(grid, 1, "Runout tol (GD&T)", self.runout_spin, "mm")
        return box

    def _group_flute(self) -> QGroupBox:
        box = QGroupBox("FLUTE")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.flute_auto = YesNoToggle(value=True)
        self.flute_auto.changed.connect(self._on_flute_auto_toggle)
        self._row(grid, 0, "Auto flute length", self.flute_auto)

        self.flute_spin = self._spin(60.0, 0.0, 100000, step=1.0)
        self.flute_spin.setEnabled(False)
        self._row(grid, 1, "Flute length", self.flute_spin, "mm")
        return box

    def _group_details(self) -> QGroupBox:
        """Title-block / template details — filled into the customer template."""
        box = QGroupBox("DRAWING DETAILS")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.customer_edit = QLineEdit()
        self.customer_edit.setPlaceholderText("Customer name")
        self._row(grid, 0, "Customer", self.customer_edit)

        self.drawn_edit = QLineEdit()
        self.drawn_edit.setPlaceholderText("Drawn by")
        self._row(grid, 1, "Drawn by", self.drawn_edit)

        self.checked_edit = QLineEdit()
        self.checked_edit.setPlaceholderText("Checked by")
        self._row(grid, 2, "Checked by", self.checked_edit)

        desc_lbl = QLabel("Description")
        desc_lbl.setObjectName("FieldLabel")
        grid.addWidget(desc_lbl, 3, 0, Qt.AlignmentFlag.AlignTop)
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText("Longer description for the title block…")
        self.desc_edit.setFixedHeight(90)
        grid.addWidget(self.desc_edit, 3, 1, 1, 2)
        return box

    def _group_derived(self) -> QGroupBox:
        box = QGroupBox("DERIVED")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        grid.setVerticalSpacing(7)

        self.read_point = self._readout(grid, 0, "Point length")
        self.read_reinf = self._readout(grid, 1, "Reinforcement length")
        self.read_body = self._readout(grid, 2, "Body length")
        self.read_flute = self._readout(grid, 3, "Flute length")

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        grid.addWidget(self.status_label, 4, 0, 1, 2)
        return box

    def _readout(self, grid: QGridLayout, r: int, key: str) -> QLabel:
        k = QLabel(key)
        k.setObjectName("ReadoutKey")
        v = QLabel("—")
        v.setObjectName("ReadoutValue")
        v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(k, r, 0)
        grid.addWidget(v, r, 1)
        return v

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
        )

    def _collect_meta(self) -> dict:
        return {
            "customer": self.customer_edit.text().strip(),
            "drawn_by": self.drawn_edit.text().strip(),
            "checked_by": self.checked_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "date": date.today().isoformat(),
        }

    def _on_input_change(self) -> None:
        self._recompute()

    def _on_reinf_toggle(self, checked: bool) -> None:
        self.reinf_angle_label.setVisible(checked)
        self.reinf_angle_spin.setVisible(checked)
        self.reinf_angle_unit.setVisible(checked)
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
                self, "MTAP — AutoCAD Link",
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
            self, f"MTAP — {title}",
            f"{type(exc).__name__}: {exc}\n\n"
            f"{traceback.format_exc()}\n"
            f"This has also been written to logs/mtap.log",
        )
