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

from app.engine.tools.drill import DrillProposalParams
from app.dxf.lsp_writer import (
    _LIBRARY, LspWriter, _num, _pt, _lsp_path,
    _BLOCK_TXT_RATIO, _GDT_REF_H, _DAT_REF_H,
)
from app.utils.logging_setup import get_logger

log = get_logger()


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
    a(';; ── proposal finalize: insert real blocks + template, native save ──')
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
  ;; Bounds come from AutoCAD's own EXTMIN/EXTMAX after a regen — robust vs.
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
;; NOTE: PROPOSAL:finish is invoked from the .scr (command context) — calling
;; it here at load time would cancel the (load) because it uses (command ...).
""")

    # accoreconsole's (load) reads .lsp as ANSI and cancels the load on UTF-8
    # multibyte chars (the reused library's comment banners use box-drawing
    # glyphs).  Sanitize to ASCII — affects comments only.
    text = "\n".join(lines).encode("ascii", "replace").decode("ascii")
    with open(lsp_path, "w", encoding="ascii") as fh:
        fh.write(text)


def finalize_with_acad(geom_dxf: str, p: DrillProposalParams,
                       anchors: dict, out_path: str) -> None:
    """Insert blocks/template via accoreconsole and write a read-write DXF.

    Falls back to the plain geometry DXF if AutoCAD isn't available, so the
    operation never hard-fails (but logs a clear warning)."""
    acc = _find_accoreconsole()
    if not acc:
        log.warning("accoreconsole not found — writing geometry-only DXF "
                    "(no template/blocks, may open read-only).")
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
    out_fwd = _lsp_path(os.path.abspath(out_path))

    with open(scr_path, "w", encoding="utf-8") as fh:
        fh.write("FILEDIA 0\n")
        fh.write("CMDDIA 0\n")
        # SECURELOAD=1 (AutoCAD 2024 default) blocks loading .lsp from untrusted
        # paths (our temp dir) -> "File load canceled".  Disable for this session.
        fh.write('(setvar "SECURELOAD" 0)\n')
        fh.write(f'(load "{_lsp_path(lsp_path)}")\n')
        fh.write("(PROPOSAL:finish)\n")     # run in command context, not at load
        fh.write(f'DXFOUT "{out_fwd}" V 2010 16\n')
        fh.write("QUIT Y\n")

    try:
        res = subprocess.run(
            [acc, "/i", geom_dxf, "/s", scr_path],
            capture_output=True, text=True, timeout=180,
        )
        log.info("accoreconsole rc=%s", res.returncode)
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
            + (res.stdout or "")[-1500:])
