"""
One-click AutoCAD setup.

Writes an `acaddoc.lsp` (auto-loaded by AutoCAD on every drawing) into each
AutoCAD per-user Support folder, so the `DMTAP` command is always available with
no manual APPLOAD. The MTAP block is fenced by markers so re-running just updates
it and never clobbers other content in an existing acaddoc.lsp.
"""
import glob
import os

_BEGIN = ";;; >>> MTAP DMTAP loader (auto-managed — do not edit between markers) >>>"
_END = ";;; <<< MTAP DMTAP loader <<<"

_BLOCK = _BEGIN + """
(vl-load-com)
(defun c:DMTAP ( / f)
  (setq f (strcat (getenv "USERPROFILE") "\\\\MTAP\\\\mtap_link.lsp"))
  (if (findfile f)
    (load f)
    (princ "\\nMTAP: click 'AutoCAD Link' in the MTAP app first, then run DMTAP."))
  (princ))
(princ "\\nMTAP ready — type DMTAP after clicking AutoCAD Link in the app.\\n")
(princ)
""" + _END + "\n"


def find_support_dirs() -> list[str]:
    """All per-user AutoCAD Support folders under %APPDATA%\\Autodesk."""
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return []
    pattern = os.path.join(appdata, "Autodesk", "AutoCAD*", "R*", "*", "Support")
    return [d for d in glob.glob(pattern) if os.path.isdir(d)]


def _write_block(path: str) -> None:
    """Create or update acaddoc.lsp at 'path', replacing any existing MTAP block."""
    existing = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            existing = f.read()
        # strip a previously-installed MTAP block, if present
        if _BEGIN in existing and _END in existing:
            head = existing.split(_BEGIN, 1)[0]
            tail = existing.split(_END, 1)[1]
            existing = (head.rstrip() + "\n" + tail.lstrip()).strip()
    merged = (existing + "\n\n" if existing.strip() else "") + _BLOCK
    with open(path, "w", encoding="utf-8") as f:
        f.write(merged)


def install_startup() -> tuple[list[str], str]:
    """
    Install the DMTAP auto-loader. Returns (written_paths, human_message).
    Falls back to %USERPROFILE%\\MTAP if no AutoCAD Support folder is found.
    """
    dirs = find_support_dirs()
    written: list[str] = []

    if dirs:
        for d in dirs:
            try:
                _write_block(os.path.join(d, "acaddoc.lsp"))
                written.append(os.path.join(d, "acaddoc.lsp"))
            except Exception:
                pass
        if written:
            return written, (
                "AutoCAD is set up.\n\n"
                "DMTAP will load automatically the next time you open a drawing "
                "(restart AutoCAD if it's open).\n\nUpdated:\n  "
                + "\n  ".join(written)
            )

    # Fallback: drop a loadable stub the user can APPLOAD once.
    home = os.path.join(os.path.expanduser("~"), "MTAP")
    os.makedirs(home, exist_ok=True)
    stub = os.path.join(home, "mtap_startup.lsp")
    with open(stub, "w", encoding="utf-8") as f:
        f.write(_BLOCK)
    return [stub], (
        "Couldn't find an AutoCAD Support folder automatically.\n\n"
        "A loader was written to:\n  " + stub + "\n\n"
        "In AutoCAD: APPLOAD → add this file to the Startup Suite (one time)."
    )
