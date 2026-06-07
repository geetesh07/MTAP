"""
Crash + diagnostic logging for MTAP.

A windowed (no-console) exe shows nothing when it crashes, so we capture
everything to log files placed in a 'logs' folder next to the exe (when frozen)
or next to the project (in development):

  logs/mtap.log         — normal operation + handled errors (full tracebacks)
  logs/mtap_fault.log   — low-level / hard crashes (segfaults) via faulthandler

Also installs a global exception hook so any uncaught error is logged AND shown
to the user in a dialog instead of silently killing the app.
"""
import os
import sys
import logging
import faulthandler
import platform

_LOGGER_NAME = "mtap"


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def log_dir() -> str:
    d = os.path.join(_base_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def setup_logging() -> str:
    """Configure logging + crash handlers. Returns the path to the main log file."""
    d = log_dir()
    log_path = os.path.join(d, "mtap.log")

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(fh)

    # Low-level crash handler (segfaults, native crashes).
    try:
        fault_file = open(os.path.join(d, "mtap_fault.log"), "a", encoding="utf-8")
        faulthandler.enable(fault_file)
    except Exception:
        pass

    logger.info("=" * 60)
    logger.info("MTAP starting")
    logger.info("frozen=%s  python=%s  platform=%s",
                getattr(sys, "frozen", False), sys.version.split()[0], platform.platform())
    logger.info("base dir: %s", _base_dir())

    # Global uncaught-exception hook: log + show dialog instead of vanishing.
    def excepthook(exc_type, exc, tb):
        logger.critical("UNCAUGHT EXCEPTION", exc_info=(exc_type, exc, tb))
        try:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None, "MTAP by NTS — Unexpected Error",
                f"{exc_type.__name__}: {exc}\n\n"
                f"Full details written to:\n{log_path}",
            )
        except Exception:
            pass

    sys.excepthook = excepthook
    return log_path


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)
