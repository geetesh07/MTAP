"""Proposal Drawing screen."""

import os
import time
import traceback

import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QFileDialog, QMessageBox, QScrollArea, QGroupBox, QGridLayout,
    QSizePolicy, QCheckBox, QProgressBar, QStackedWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, pyqtSlot, QTimer
from PyQt6.QtGui import QFont

from app.engine.tools.drill import DrillProposalParams
from app.ui.widgets import NoScrollDoubleSpinBox, NoScrollComboBox
from app.utils.logging_setup import get_logger

log = get_logger()

_DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "MTAP", "proposals")


# ── numpy helper (runs on worker thread, zero UI imports) ─────────────────────

def _build_vbo(verts: list, indices: list):
    """Compute interleaved VBO bytes + camera params from raw OCC mesh.
    Called on the WORKER thread so the main thread never freezes.
    Returns (vbo_bytes, n_verts, cx, cy, cz, scale).
    """
    v = np.array(verts,   dtype=np.float32).reshape(-1, 3)
    i = np.array(indices, dtype=np.int32  ).reshape(-1, 3)

    # Smooth per-vertex normals (accumulate face normals)
    norms = np.zeros_like(v)
    v0, v1, v2 = v[i[:, 0]], v[i[:, 1]], v[i[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    np.add.at(norms, i[:, 0], fn)
    np.add.at(norms, i[:, 1], fn)
    np.add.at(norms, i[:, 2], fn)
    mag = np.linalg.norm(norms, axis=1, keepdims=True)
    mag[mag < 1e-10] = 1.0
    norms = (norms / mag).astype(np.float32)

    # Expand indexed mesh → flat triangles (glDrawArrays, no EBO needed)
    flat  = i.flatten()
    data  = np.hstack([v[flat], norms[flat]]).astype(np.float32)

    # Camera: fit to bounding box
    mn, mx = v.min(axis=0), v.max(axis=0)
    c = (mn + mx) * 0.5
    scale = max(float(np.linalg.norm(mx - mn)), 1.0)

    return data.tobytes(), len(flat), float(c[0]), float(c[1]), float(c[2]), scale


# ═════════════════════════════════════════════════════════ Preview worker ══

class _PreviewWorker(QThread):
    """Builds the solid and tessellates — no DXF, no node.js, no AutoCAD.
    Typical time: 3-8 s for any drill.
    """
    finished   = pyqtSignal()
    errored    = pyqtSignal(str, str)
    progress   = pyqtSignal(int, str)
    mesh_ready = pyqtSignal()

    def __init__(self, params: DrillProposalParams):
        super().__init__()
        self._params = params
        self._mesh_payload: tuple | None = None

    def run(self) -> None:
        def _on_progress(pct: int, msg: str) -> None:
            self.progress.emit(pct, msg)

        def _on_mesh(verts: list, indices: list) -> None:
            self._mesh_payload = _build_vbo(verts, indices)
            self.mesh_ready.emit()

        try:
            from app.dxf.proposal_dxf import preview_solid
            preview_solid(self._params, progress=_on_progress, mesh_cb=_on_mesh)
            self.finished.emit()
        except Exception as exc:
            self.errored.emit(str(exc), traceback.format_exc())


# ═════════════════════════════════════════════════════════ DXF worker ══

class _GenerateWorker(QThread):
    finished   = pyqtSignal(str)    # out_path on success
    errored    = pyqtSignal(str, str)
    progress   = pyqtSignal(int, str)
    # mesh_ready carries NO data — the caller reads self._mesh_payload directly.
    # Avoids copying 300K+ Python list items through the signal machinery.
    mesh_ready = pyqtSignal()

    def __init__(self, params: DrillProposalParams, out_path: str):
        super().__init__()
        self._params   = params
        self._out_path = out_path
        # Set by _on_mesh (worker thread) before mesh_ready is emitted.
        # Read by _on_mesh_ready slot (main thread) after the signal is received.
        # Safe: write happens-before signal emit, signal receive happens-before read.
        self._mesh_payload: tuple | None = None

    def run(self) -> None:
        def _on_progress(pct: int, msg: str) -> None:
            self.progress.emit(pct, msg)

        def _on_mesh(verts: list, indices: list) -> None:
            # Heavy numpy work on the WORKER thread — main thread stays live.
            self._mesh_payload = _build_vbo(verts, indices)
            self.mesh_ready.emit()   # lightweight zero-arg signal

        try:
            from app.dxf.proposal_dxf import generate as generate_dxf
            generate_dxf(self._params, self._out_path,
                         progress=_on_progress, mesh_cb=_on_mesh)
            self.finished.emit(self._out_path)
        except PermissionError as exc:
            self.errored.emit(
                f"Permission denied — close the file in AutoCAD first.\n\n{exc}",
                traceback.format_exc(),
            )
        except Exception as exc:
            self.errored.emit(str(exc), traceback.format_exc())


# ═════════════════════════════════════════════════════════ STEP worker ══

class _StepWorker(QThread):
    finished = pyqtSignal(str)
    errored  = pyqtSignal(str, str)
    progress = pyqtSignal(int, str)

    def __init__(self, params: DrillProposalParams, step_path: str):
        super().__init__()
        self._params    = params
        self._step_path = step_path

    def run(self) -> None:
        def _on_progress(pct: int, msg: str) -> None:
            self.progress.emit(pct, msg)
        try:
            from app.dxf.proposal_dxf import generate_step
            generate_step(self._params, self._step_path, progress=_on_progress)
            self.finished.emit(self._step_path)
        except Exception as exc:
            self.errored.emit(str(exc), traceback.format_exc())


# ═════════════════════════════════════════════════════════ Screen ══

class ProposalScreen(QWidget):
    back_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._worker:         _GenerateWorker | None = None
        self._step_worker:    _StepWorker     | None = None
        self._preview_worker: _PreviewWorker  | None = None
        self._gen_start: float = 0.0
        self._viewer = None   # DrillPreview3D or None if GL unavailable

        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._update_elapsed)

        self._build_ui()

    # ═══════════════════════════════════════════════════════════════ build ══

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        split = QHBoxLayout()
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)
        split.addWidget(self._build_params_panel(), 0)
        split.addWidget(self._build_viewer_panel(), 1)

        body = QWidget()
        body.setLayout(split)
        root.addWidget(body, 1)

        root.addWidget(self._build_action_bar())

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

    # ─── left: params ─────────────────────────────────────────────────────────

    def _build_params_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(420)

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        form = QWidget()
        page = QVBoxLayout(form)
        page.setContentsMargins(28, 20, 20, 20)
        page.setSpacing(16)

        hdr = QLabel("PARAMETERS")
        hdr.setObjectName("SectionHeader")
        page.addWidget(hdr)

        page.addWidget(self._group_dimensions())
        page.addWidget(self._group_geometry())
        page.addWidget(self._group_flute())
        page.addWidget(self._group_info())
        page.addStretch()

        scroll.setWidget(form)
        outer.addWidget(scroll)
        return panel

    # ─── right: 3D viewer ─────────────────────────────────────────────────────

    def _build_viewer_panel(self) -> QWidget:
        panel = QWidget()
        lo = QVBoxLayout(panel)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        placeholder = QLabel("3D preview\n\nGenerate a DXF to see\nthe drill solid here.")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setObjectName("PlaceholderSubText")
        placeholder.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)

        try:
            from app.ui.drill_preview_3d import DrillPreview3D
            self._viewer = DrillPreview3D()
        except Exception:
            self._viewer = None
            log.warning("DrillPreview3D unavailable — OpenGL init failed.",
                        exc_info=True)

        self._view_stack = QStackedWidget()
        self._view_stack.addWidget(placeholder)          # index 0
        if self._viewer is not None:
            self._view_stack.addWidget(self._viewer)     # index 1
        self._view_stack.setCurrentIndex(0)

        lo.addWidget(self._view_stack, 1)

        hint = QLabel("Left-drag: orbit   ·   Scroll: zoom")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setObjectName("PlaceholderSubText")
        hint.setFixedHeight(22)
        lo.addWidget(hint)

        return panel

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

        self._pa    = self._spin(60.0, 160.0, 118.0, 1, "°")
        self._reinf = QCheckBox("Reinforcement (conical neck)")
        self._ra    = self._spin(5.0, 89.0, 30.0, 1, "°")
        self._runout = self._spin(0.0, 1.0, 0.010, 3, " mm")

        self._reinf.toggled.connect(self._ra.setEnabled)
        self._ra.setEnabled(self._reinf.isChecked())

        grid.addWidget(QLabel("Point Angle"),   0, 0)
        grid.addWidget(self._pa,                0, 1)
        grid.addWidget(self._reinf,             1, 0, 1, 2)
        grid.addWidget(QLabel("Reinf. Angle"),  2, 0)
        grid.addWidget(self._ra,                2, 1)
        grid.addWidget(QLabel("Runout (GD&T)"), 3, 0)
        grid.addWidget(self._runout,            3, 1)
        return g

    def _group_flute(self) -> QGroupBox:
        g = QGroupBox("FLUTE")
        grid = QGridLayout(g)
        grid.setColumnStretch(1, 1)
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(16)

        self._helix   = self._spin(5.0, 85.0, 30.0, 1, "°")
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
            "Generates a production proposal DXF: a real 3D solid is built and\n"
            "projected to clean side + end views (edge projection), with\n"
            "centerlines, dimensions, GD&T runout, and a title block.\n\n"
            "Requires Node.js installed on this machine.\n\n"
            "EXPORT STEP saves the 3D solid as a STEP (AP203) file\n"
            "importable into any CAD system."
        )
        lbl.setObjectName("PlaceholderSubText")
        lbl.setWordWrap(True)
        lo.addWidget(lbl)
        return g

    # ─── action bar ───────────────────────────────────────────────────────────

    def _build_action_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(72)
        lo = QVBoxLayout(bar)
        lo.setContentsMargins(24, 6, 24, 8)
        lo.setSpacing(4)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setVisible(False)
        lo.addWidget(self._progress_bar)

        row = QHBoxLayout()
        row.setSpacing(10)

        self._stage_label = QLabel("")
        self._stage_label.setObjectName("PlaceholderSubText")
        self._stage_label.setSizePolicy(QSizePolicy.Policy.Expanding,
                                         QSizePolicy.Policy.Preferred)

        self._elapsed_label = QLabel("")
        self._elapsed_label.setObjectName("PlaceholderSubText")
        self._elapsed_label.setFixedWidth(64)
        self._elapsed_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                         | Qt.AlignmentFlag.AlignVCenter)

        # Reset inputs button
        self._reset_btn = QPushButton("RESET")
        self._reset_btn.setObjectName("SecondaryButton")
        self._reset_btn.setFixedWidth(90)
        self._reset_btn.setFixedHeight(40)
        self._reset_btn.clicked.connect(self._on_reset)

        # Primary: preview 3D (fast, no DXF, no AutoCAD)
        self._preview_btn = QPushButton("PREVIEW 3D")
        self._preview_btn.setObjectName("PrimaryButton")
        self._preview_btn.setFixedWidth(150)
        self._preview_btn.setFixedHeight(40)
        self._preview_btn.clicked.connect(self._on_preview)

        # Secondary: export STEP
        self._step_btn = QPushButton("EXPORT STEP")
        self._step_btn.setObjectName("SecondaryButton")
        self._step_btn.setFixedWidth(140)
        self._step_btn.setFixedHeight(40)
        self._step_btn.clicked.connect(self._on_export_step)

        # Tertiary: full DXF with AutoCAD finalization
        self._btn = QPushButton("GENERATE DXF")
        self._btn.setObjectName("SecondaryButton")
        self._btn.setFixedWidth(140)
        self._btn.setFixedHeight(40)
        self._btn.clicked.connect(self._on_generate)

        row.addWidget(self._reset_btn)
        row.addWidget(self._stage_label)
        row.addWidget(self._elapsed_label)
        row.addWidget(self._preview_btn)
        row.addWidget(self._step_btn)
        row.addWidget(self._btn)
        lo.addLayout(row)

        return bar

    # ─── helpers ──────────────────────────────────────────────────────────────

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
            cutting_diameter    = self._dc.value(),
            shank_diameter      = self._d.value(),
            overall_length      = self._oal.value(),
            shank_length        = self._ls.value(),
            point_angle         = self._pa.value(),
            helix_angle         = self._helix.value(),
            n_flutes            = int(self._nflutes.currentText().split()[0]),
            reinforcement       = self._reinf.isChecked(),
            reinforcement_angle = self._ra.value(),
            runout              = self._runout.value(),
        )
        p.derive()
        return p

    def _set_busy(self, busy: bool, mode: str = "preview") -> None:
        # Disable all three buttons while any operation is running
        self._preview_btn.setEnabled(not busy)
        self._step_btn.setEnabled(not busy)
        self._btn.setEnabled(not busy)

        self._preview_btn.setText("Building…"   if (busy and mode == "preview") else "PREVIEW 3D")
        self._step_btn.setText("Exporting…"     if (busy and mode == "step")    else "EXPORT STEP")
        self._btn.setText("Generating…"         if (busy and mode == "dxf")     else "GENERATE DXF")

    def _start_progress(self) -> None:
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._stage_label.setText("")
        self._elapsed_label.setText("0.0s")
        self._gen_start = time.monotonic()
        self._tick.start()

    def _stop_progress(self) -> None:
        self._tick.stop()
        elapsed = time.monotonic() - self._gen_start if self._gen_start > 0 else 0.0
        self._gen_start = 0.0
        return elapsed

    # ─── reset ────────────────────────────────────────────────────────────────

    def _on_reset(self) -> None:
        self._dc.setValue(10.0)
        self._d.setValue(10.0)
        self._oal.setValue(100.0)
        self._ls.setValue(35.0)
        self._pa.setValue(118.0)
        self._reinf.setChecked(False)
        self._ra.setValue(30.0)
        self._runout.setValue(0.010)
        self._helix.setValue(30.0)
        self._nflutes.setCurrentIndex(0)

    # ─── DXF generation ───────────────────────────────────────────────────────

    def _on_generate(self) -> None:
        try:
            params = self._read_params()
            errs = params.validate()
            if errs:
                self._stage_label.setText("⚠ " + "  ·  ".join(errs))
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

            self._view_stack.setCurrentIndex(0)
            if self._viewer is not None:
                self._viewer.clear()

            self._set_busy(True, "dxf")
            self._start_progress()

            self._worker = _GenerateWorker(params, path)
            self._worker.finished.connect(self._on_dxf_done)
            self._worker.errored.connect(self._on_dxf_error)
            self._worker.progress.connect(self._on_progress)
            self._worker.mesh_ready.connect(self._on_mesh_ready)
            self._worker.start()

        except Exception:
            tb = traceback.format_exc()
            log.error("Proposal DXF setup failed:\n%s", tb)
            self._stage_label.setText("Error — see log for details.")
            QMessageBox.critical(self, "Error", tb)

    # ─── STEP export ──────────────────────────────────────────────────────────

    def _on_export_step(self) -> None:
        try:
            params = self._read_params()
            errs = params.validate()
            if errs:
                self._stage_label.setText("⚠ " + "  ·  ".join(errs))
                return

            Dc  = params.cutting_diameter
            OAL = params.overall_length
            name = f"Drill_Dc{Dc:g}_OAL{OAL:g}.stp"
            os.makedirs(_DEFAULT_DIR, exist_ok=True)
            default_path = os.path.join(_DEFAULT_DIR, name)

            path, _ = QFileDialog.getSaveFileName(
                self, "Export STEP", default_path, "STEP Files (*.stp *.step)"
            )
            if not path:
                return

            self._set_busy(True, "step")
            self._start_progress()

            self._step_worker = _StepWorker(params, path)
            self._step_worker.finished.connect(self._on_step_done)
            self._step_worker.errored.connect(self._on_step_error)
            self._step_worker.progress.connect(self._on_progress)
            self._step_worker.start()

        except Exception:
            tb = traceback.format_exc()
            log.error("STEP export setup failed:\n%s", tb)
            self._stage_label.setText("Error — see log for details.")
            QMessageBox.critical(self, "Error", tb)

    # ─── slots ────────────────────────────────────────────────────────────────

    @pyqtSlot(int, str)
    def _on_progress(self, pct: int, msg: str) -> None:
        self._progress_bar.setValue(pct)
        self._stage_label.setText(msg)
        self._update_elapsed()

    @pyqtSlot()
    def _on_mesh_ready(self) -> None:
        if self._viewer is None:
            # OpenGL widget failed to initialise — surface this visibly
            self._stage_label.setText(
                "3D viewer unavailable — OpenGL 3.3 init failed (check log)")
            log.error("_on_mesh_ready: self._viewer is None — "
                      "DrillPreview3D failed to construct; check earlier log warning.")
            return
        # Accept mesh from whichever worker is active (preview or DXF)
        worker = self._preview_worker if self._preview_worker is not None else self._worker
        if worker is None:
            log.error("_on_mesh_ready: no active worker found")
            return
        payload = worker._mesh_payload
        if payload is None:
            log.error("_on_mesh_ready: worker._mesh_payload is None")
            return
        vbo_bytes, n_verts, cx, cy, cz, scale = payload
        log.debug("_on_mesh_ready: %d verts, switching to viewer", n_verts)
        self._view_stack.setCurrentIndex(1)
        self._viewer.load_bytes(vbo_bytes, n_verts, cx, cy, cz, scale)

    def _update_elapsed(self) -> None:
        if self._gen_start > 0:
            self._elapsed_label.setText(
                f"{time.monotonic() - self._gen_start:.1f}s")

    @pyqtSlot(str)
    def _on_dxf_done(self, path: str) -> None:
        elapsed = self._stop_progress()
        self._set_busy(False, "dxf")
        self._progress_bar.setValue(100)
        self._stage_label.setText(f"Saved: {os.path.basename(path)}")
        self._elapsed_label.setText(f"{elapsed:.1f}s")
        log.info("Proposal DXF: %s", path)

        reply = QMessageBox.question(
            self, "Open DXF?",
            f"DXF saved.\n\nOpen {os.path.basename(path)} now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            os.startfile(path)

    @pyqtSlot(str, str)
    def _on_dxf_error(self, message: str, tb: str) -> None:
        elapsed = self._stop_progress()
        self._set_busy(False, "dxf")
        self._progress_bar.setVisible(False)
        self._elapsed_label.setText(f"{elapsed:.1f}s" if elapsed > 0 else "")
        log.error("Proposal DXF failed:\n%s", tb)

        if "Permission denied" in message or "PermissionError" in message:
            self._stage_label.setText(
                "Permission denied — close the file in AutoCAD first.")
            QMessageBox.warning(
                self, "File in Use",
                f"Cannot save — the file is open in another application (AutoCAD).\n\n"
                f"Close the DXF in AutoCAD, then click Generate again.\n\n{message}",
            )
        else:
            self._stage_label.setText("Error — see log for details.")
            QMessageBox.critical(self, "Error", tb)

    @pyqtSlot(str)
    def _on_step_done(self, path: str) -> None:
        elapsed = self._stop_progress()
        self._set_busy(False, "step")
        self._progress_bar.setValue(100)
        self._stage_label.setText(f"STEP saved: {os.path.basename(path)}")
        self._elapsed_label.setText(f"{elapsed:.1f}s")
        log.info("STEP export: %s", path)

        reply = QMessageBox.question(
            self, "Open STEP?",
            f"STEP file saved.\n\nOpen {os.path.basename(path)} now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            os.startfile(path)

    @pyqtSlot(str, str)
    def _on_step_error(self, message: str, tb: str) -> None:
        elapsed = self._stop_progress()
        self._set_busy(False, "step")
        self._progress_bar.setVisible(False)
        self._elapsed_label.setText(f"{elapsed:.1f}s" if elapsed > 0 else "")
        log.error("STEP export failed:\n%s", tb)
        self._stage_label.setText("STEP export failed — see log.")
        QMessageBox.critical(self, "Error", tb)

    # ─── 3D preview ───────────────────────────────────────────────────────────

    def _on_preview(self) -> None:
        try:
            params = self._read_params()
            errs = params.validate()
            if errs:
                self._stage_label.setText("⚠ " + "  ·  ".join(errs))
                return

            self._view_stack.setCurrentIndex(0)
            if self._viewer is not None:
                self._viewer.clear()

            self._set_busy(True, "preview")
            self._start_progress()

            self._preview_worker = _PreviewWorker(params)
            self._preview_worker.finished.connect(self._on_preview_done)
            self._preview_worker.errored.connect(self._on_preview_error)
            self._preview_worker.progress.connect(self._on_progress)
            self._preview_worker.mesh_ready.connect(self._on_mesh_ready)
            self._preview_worker.start()

        except Exception:
            tb = traceback.format_exc()
            log.error("Preview setup failed:\n%s", tb)
            self._stage_label.setText("Error — see log for details.")
            QMessageBox.critical(self, "Error", tb)

    @pyqtSlot()
    def _on_preview_done(self) -> None:
        elapsed = self._stop_progress()
        self._set_busy(False, "preview")
        self._progress_bar.setValue(100)
        self._stage_label.setText("3D model ready")
        self._elapsed_label.setText(f"{elapsed:.1f}s")

    @pyqtSlot(str, str)
    def _on_preview_error(self, message: str, tb: str) -> None:
        elapsed = self._stop_progress()
        self._set_busy(False, "preview")
        self._progress_bar.setVisible(False)
        self._elapsed_label.setText(f"{elapsed:.1f}s" if elapsed > 0 else "")
        log.error("Preview failed:\n%s", tb)
        self._stage_label.setText("Preview failed — see log.")
        QMessageBox.critical(self, "Preview Error", tb)
