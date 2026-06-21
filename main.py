import sys
import os

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt6.QtCore import Qt

from app.ui.main_window import MainWindow
from app.utils.config import APP_NAME, APP_VERSION, resource_path
from app.utils.logging_setup import setup_logging, get_logger, log_dir


def _create_icon() -> QIcon:
    """Programmatic icon: dark background, amber M."""
    px = QPixmap(64, 64)
    px.fill(QColor("#1a1a1a"))

    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Amber border
    painter.setPen(QColor("#c8a800"))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(2, 2, 60, 60)

    # "M" letter
    painter.setPen(QColor("#c8a800"))
    font = QFont("Segoe UI", 36, QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "M")

    painter.end()
    return QIcon(px)


def run_selftest() -> None:
    """
    Headless check of the deliverable pipeline (AutoCAD link). Confirms the
    FROZEN exe can extract the embedded blocks and write the DMTAP link file
    without the GUI. Results go to logs/selftest_result.txt and logs/mtap.log.
    Run with:  MTAP.exe --selftest
    """
    setup_logging()
    log = get_logger()
    from app.engine.tools.drill import DrillBlankParams
    from app.dxf.lsp_writer import LspWriter
    from app.utils.config import AUTOCAD_LINK_PATH

    lines = [f"link path: {AUTOCAD_LINK_PATH}"]

    p = DrillBlankParams(cutting_diameter=8, shank_diameter=12, overall_length=120,
                         shank_length=45, point_angle=140, reinforcement=True,
                         reinforcement_angle=30)
    p.derive()
    lines.append(f"validate: {p.validate() or 'OK'}")

    meta = {"customer": "SELFTEST", "drawn_by": "MTAP", "checked_by": "",
            "description": "self-test drawing"}
    try:
        LspWriter(p, meta).generate(AUTOCAD_LINK_PATH)
        size = os.path.getsize(AUTOCAD_LINK_PATH) if os.path.exists(AUTOCAD_LINK_PATH) else 0
        lines.append(f"LINK: {'OK' if size > 0 else 'EMPTY FILE'} size={size}")
    except Exception as e:
        log.exception("selftest LINK failed")
        lines.append(f"LINK: FAIL {type(e).__name__}: {e}")

    text = "\n".join(lines)
    log.info("SELFTEST RESULT:\n%s", text)
    with open(os.path.join(log_dir(), "selftest_result.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)


def _child_prefix() -> list:
    """Command prefix that re-invokes THIS program for a single-DXF child run."""
    if getattr(sys, "frozen", False):
        return [sys.executable]                 # the exe itself
    return [sys.executable, os.path.abspath(__file__)]   # python main.py


def run_gen_proposals(out_root: str) -> None:
    """Headless: generate the full proposal-DXF matrix into out_root, one fresh
    child process per DXF (OpenCASCADE state must not accumulate in the exe).
    Usage:  MTAP.exe --gen-proposals "C:\\path\\to\\output_folder" """
    setup_logging()
    log = get_logger()
    from app.dxf.proposal_batch import generate_matrix_isolated
    log.info("Generating proposal matrix into %s", out_root)
    n = generate_matrix_isolated(out_root, _child_prefix(),
                                 log=lambda m: (print(m), log.info(m)))
    print(f"\nGenerated {n} DXFs into {out_root}")


def run_gen_one(name: str, out_path: str) -> None:
    """Headless single-DXF generation (one matrix case). Fresh process per call."""
    setup_logging()
    from app.dxf.proposal_batch import generate_one
    generate_one(name, out_path)


def main() -> None:
    if "--selftest" in sys.argv:
        run_selftest()
        return

    if "--gen-one" in sys.argv:
        i = sys.argv.index("--gen-one")
        run_gen_one(sys.argv[i + 1], sys.argv[i + 2])
        return

    if "--gen-proposals" in sys.argv:
        i = sys.argv.index("--gen-proposals")
        out_root = sys.argv[i + 1] if i + 1 < len(sys.argv) else os.path.join(
            os.path.expanduser("~"), "Desktop", "MTAP_Proposals")
        run_gen_proposals(out_root)
        return

    log_path = setup_logging()
    log = get_logger()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("MTAP")
    log.info("QApplication created; log file: %s", log_path)

    # Apply the saved theme (dark/light); injects arrow-icon paths internally.
    from app.ui import theme
    theme.apply_theme(theme.load_saved())

    # Prefer the packaged .ico if present, else fall back to the drawn icon.
    ico_path = resource_path(os.path.join("assets", "icons", "mtap.ico"))
    app.setWindowIcon(QIcon(ico_path) if os.path.exists(ico_path) else _create_icon())

    window = MainWindow()
    window.showMaximized()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
