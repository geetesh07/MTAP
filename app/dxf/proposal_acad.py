"""
proposal_acad.py — AutoCAD finalize stage for the Proposal Drawing.

ezdxf cannot read the user's DWG blocks, and ezdxf-authored DXFs open read-only
in AutoCAD.  So the final step runs the geometry DXF through AutoCAD's headless
engine (accoreconsole.exe):

  * insert the REAL DWG blocks  MTAP_GDT + MTAP_DATUM (runout) and wrap the whole
    drawing in MTAP_TEMPLATE (auto-scaled, so it never overlaps the model)
  * DXFOUT the result — a file authored by AutoCAD itself opens read-WRITE.

Reuses the Blank mode's debugged LISP library (place-template, ins-block,
set-attrib, range-bbox, load-block) verbatim — see app/dxf/lsp_writer.py.
"""

import glob
import os
import subprocess
import tempfile

import ezdxf

from app.engine.tools.drill import DrillProposalParams
from app.dxf.lsp_writer import (
    _LIBRARY, LspWriter, _num, _pt, _lsp_path,
    _BLOCK_TXT_RATIO, _GDT_REF_H, _DAT_REF_H,
)
from app.utils.logging_setup import get_logger

log = get_logger()

# Blocks that MUST appear as INSERTs in the finalised DXF.
# MTAP_TEMPLATE is always required; GDT/DATUM only when runout > 0.
_REQUIRED_BLOCKS_ALWAYS = {"MTAP_TEMPLATE"}
_REQUIRED_BLOCKS_GDT    = {"MTAP_GDT", "MTAP_DATUM"}


def _find_accoreconsole() -> str | None:
    """Locate accoreconsole.exe from any installed AutoCAD, newest first."""
    roots = [
        r"C:\Program Files\Autodesk",
        r"C:\Program Files (x86)\Autodesk",
    ]
    hits = []
    for root in roots:
        hits += glob.glob(os.path.join(root, "AutoCAD *", "accoreconsole.exe"))
    if not hits:
        return None
    # newest AutoCAD year wins (lexical sort puts 2024 after 2020)
    hits.sort(reverse=True)
    return hits[0]


def _build_finish_lsp(p: DrillProposalParams, anchors: dict, blocks_dir: str,
                      lsp_path: str) -> None:
    bd = _lsp_path(blocks_dir)

    # block text scale = dim-text height * ratio / block reference height
    feature   = max(p.overall_length, p.cutting_diameter * 4.0,
                    p.effective_shank_diameter * 4.0, 1.0)
    block_txt = feature * 0.0154 * _BLOCK_TXT_RATIO
    scale_gdt = block_txt / _GDT_REF_H
    scale_dat = block_txt / _DAT_REF_H

    has_gdt = p.runout > 0
    gx, gy  = anchors["gdt_ins"]
    dx, dy  = anchors["dat_ins"]

    lines = [_LIBRARY]
    a = lines.append
    a(';; -- proposal finalize: insert real blocks + template, native save --')
    a(f'(setq PR:PATH_GDT      "{bd}/MTAP_GDT.dwg")')
    a(f'(setq PR:PATH_DATUM    "{bd}/MTAP_DATUM.dwg")')
    a(f'(setq PR:PATH_TEMPLATE "{bd}/MTAP_TEMPLATE.dwg")')
    a(f'(setq PR:HASGDT   {"T" if has_gdt else "nil"})')
    a(f'(setq PR:GDTINS   {_pt(gx, gy)})')
    a(f'(setq PR:DATINS   {_pt(dx, dy)})')
    a(f'(setq PR:GDTVAL   "{p.runout:.3f}")')
    a(f'(setq PR:SCALE_GDT {_num(scale_gdt)})')
    a(f'(setq PR:SCALE_DAT {_num(scale_dat)})')
    a("")
    a(r"""
(defun PROPOSAL:finish ( / blk osm cme emin emax bb)
  (setq osm (getvar "OSMODE") cme (getvar "CMDECHO"))
  (setvar "OSMODE" 0) (setvar "CMDECHO" 0) (setvar "INSUNITS" 0)
  (MTAP:make-layer "MTAP-GDT" 2 "Continuous")
  ;; (re)define the real blocks from the user's DWG files
  (MTAP:load-block "MTAP_GDT"      PR:PATH_GDT)
  (MTAP:load-block "MTAP_DATUM"    PR:PATH_DATUM)
  (MTAP:load-block "MTAP_TEMPLATE" PR:PATH_TEMPLATE)
  ;; circular-runout frame on Dc + datum on the shank
  (if PR:HASGDT
    (progn
      (setvar "CLAYER" "MTAP-GDT")
      (setq blk (MTAP:ins-block "MTAP_GDT" PR:GDTINS PR:SCALE_GDT))
      (MTAP:set-attrib blk "VAL" PR:GDTVAL)
      (MTAP:ins-block "MTAP_DATUM" PR:DATINS PR:SCALE_DAT)))
  ;; wrap the whole drawing in the title-block template (auto-scaled w/ margin).
  ;; Bounds come from AutoCAD's own EXTMIN/EXTMAX after a regen -- robust vs.
  ;; iterating entities (some convert to a nil VLA-object and abort).
  (setvar "CLAYER" "0")
  (command "_.ZOOM" "_Extents")
  (setq emin (getvar "EXTMIN") emax (getvar "EXTMAX"))
  (setq bb (list (list (car emin) (cadr emin))
                 (list (car emax) (cadr emax))))
  (MTAP:place-template bb)
  (command "_.ZOOM" "_Extents")
  (setvar "OSMODE" osm) (setvar "CMDECHO" cme)
  (princ))
;; NOTE: PROPOSAL:finish is invoked from the .scr (command context) -- calling
;; it here at load time would cancel the (load) because it uses (command ...).
""")

    # accoreconsole's (load) reads .lsp as ANSI and cancels the load on UTF-8
    # multibyte chars (the reused library's comment banners use box-drawing
    # glyphs).  Sanitize to ASCII -- affects comments only.
    text = "\n".join(lines).encode("ascii", "replace").decode("ascii")
    with open(lsp_path, "w", encoding="ascii") as fh:
        fh.write(text)


def _verify_output(out_path: str, require_gdt: bool) -> None:
    """Re-open the finalised DXF with ezdxf and assert required INSERTs exist.

    Raises RuntimeError if any required block INSERT is missing.  This is the
    post-condition check that catches silent failures (LISP errored partway
    through but DXFOUT still wrote a geometry-only file)."""
    try:
        doc = ezdxf.readfile(out_path)
    except Exception as exc:
        raise RuntimeError(f"Could not re-open finalised DXF for verification: {exc}")

    found = {e.dxf.name for e in doc.modelspace()
             if e.dxftype() == "INSERT"}

    required = set(_REQUIRED_BLOCKS_ALWAYS)
    if require_gdt:
        required |= _REQUIRED_BLOCKS_GDT

    missing = required - found
    if missing:
        raise RuntimeError(
            f"Finalised DXF is missing required block INSERTs: "
            f"{sorted(missing)}.  "
            f"AutoCAD LISP likely failed partway — check log for LISP errors.")


def finalize_with_acad(geom_dxf: str, p: DrillProposalParams,
                       anchors: dict, out_path: str,
                       require_acad: bool = False) -> None:
    """Insert blocks/template via accoreconsole and write a read-write DXF.

    Args:
        require_acad: if True, raise instead of falling back when AutoCAD is
            absent (use this in batch / CLI mode so failures are visible).
    """
    acc = _find_accoreconsole()
    if not acc:
        msg = ("accoreconsole not found — cannot insert real blocks / template. "
               "Install AutoCAD or run without require_acad.")
        if require_acad:
            raise RuntimeError(msg)
        log.warning("%s  Writing geometry-only DXF (may open read-only).", msg)
        import shutil
        shutil.copyfile(geom_dxf, out_path)
        return

    tmpdir    = tempfile.mkdtemp(prefix="mtap_prop_")
    lsp_path  = os.path.join(tmpdir, "proposal_finish.lsp")
    scr_path  = os.path.join(tmpdir, "proposal.scr")
    blocks    = LspWriter._resolve_blocks_dir(tmpdir)

    _build_finish_lsp(p, anchors, blocks, lsp_path)

    # remove any stale output so DXFOUT never hits an overwrite prompt
    if os.path.exists(out_path):
        os.remove(out_path)

    # Escape backslashes and double-quotes so the path is safe inside a DXF
    # script string.  Windows paths never legally contain " but be explicit.
    out_fwd = _lsp_path(os.path.abspath(out_path)).replace('"', '\\"')
    lsp_fwd = _lsp_path(lsp_path).replace('"', '\\"')

    with open(scr_path, "w", encoding="utf-8") as fh:
        fh.write("FILEDIA 0\n")
        fh.write("CMDDIA 0\n")
        # SECURELOAD=1 (AutoCAD 2024 default) blocks loading .lsp from temp dirs.
        # Disable for the (load) call only, then immediately re-enable.
        fh.write('(setvar "SECURELOAD" 0)\n')
        fh.write(f'(load "{lsp_fwd}")\n')
        fh.write('(setvar "SECURELOAD" 1)\n')   # re-enable ASAP after load
        fh.write("(PROPOSAL:finish)\n")          # run in command context, not at load
        fh.write(f'DXFOUT "{out_fwd}" V 2010 16\n')
        fh.write("QUIT Y\n")

    stdout_raw = b""
    try:
        res = subprocess.run(
            [acc, "/i", geom_dxf, "/s", scr_path],
            capture_output=True, timeout=180,
        )
        stdout_raw = res.stdout or b""
        # accoreconsole emits UTF-16LE; fall back to latin-1 if that fails
        try:
            stdout_txt = stdout_raw.decode("utf-16-le", errors="replace")
        except Exception:
            stdout_txt = stdout_raw.decode("latin-1", errors="replace")

        log.info("accoreconsole rc=%s", res.returncode)
        if stdout_txt.strip():
            log.debug("accoreconsole stdout:\n%s", stdout_txt[:3000])

        if res.returncode not in (0, 1):  # rc=1 is normal "quit" exit for acad
            raise RuntimeError(
                f"accoreconsole exited with rc={res.returncode}.\n"
                + stdout_txt[-1500:])

    except subprocess.TimeoutExpired:
        raise RuntimeError("AutoCAD (accoreconsole) timed out finalizing the DXF.")
    finally:
        for f in (lsp_path, scr_path):
            try:
                os.remove(f)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    if not os.path.exists(out_path):
        raise RuntimeError(
            "AutoCAD finalize produced no output DXF.\n"
            + (stdout_txt if 'stdout_txt' in dir() else str(stdout_raw[-1500:])))

    # Post-write verification: assert the required block INSERTs are present.
    # LISP errors can still allow DXFOUT to run, producing geometry-only output.
    _verify_output(out_path, require_gdt=(p.runout > 0))
