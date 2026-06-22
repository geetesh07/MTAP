"""
proposal_acad.py — AutoCAD finalize stage for the Proposal Drawing.

Two finalization paths are available:

  1. finalize_with_ezdxf()  — pure-Python, no AutoCAD required.
     Reads the DXF versions of the blocks (autocad/blocks/*.dxf), imports them
     with ezdxf's xref.Loader, inserts GDT/DATUM/TEMPLATE, and saves directly.
     Fast (~0 s overhead).  Used when DXF block files exist.

  2. finalize_with_acad()   — legacy accoreconsole path.
     Requires AutoCAD installed.  Loads DWG blocks via LISP, runs DXFOUT so the
     output file is authored by AutoCAD itself (opens read-WRITE in AutoCAD).
     Fallback when DXF blocks are absent.

finalize_with_acad() tries path 1 first; path 2 is the fallback.
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
from app.utils.config import resource_path

log = get_logger()

# Blocks that MUST appear as INSERTs in the finalised DXF.
# MTAP_TEMPLATE is always required; GDT/DATUM only when runout > 0.
_REQUIRED_BLOCKS_ALWAYS = {"MTAP_TEMPLATE"}
_REQUIRED_BLOCKS_GDT    = {"MTAP_GDT", "MTAP_DATUM"}


def _find_accoreconsole() -> str | None:
    """Locate accoreconsole.exe from any installed AutoCAD, newest first."""
    # Explicit known paths first — glob can silently fail inside frozen exes.
    known_years = range(2030, 2018, -1)
    for root in (r"C:\Program Files\Autodesk", r"C:\Program Files (x86)\Autodesk"):
        for year in known_years:
            p = os.path.join(root, f"AutoCAD {year}", "accoreconsole.exe")
            if os.path.isfile(p):
                return p
    # Glob fallback for non-standard installs
    roots = [
        r"C:\Program Files\Autodesk",
        r"C:\Program Files (x86)\Autodesk",
    ]
    hits = []
    for root in roots:
        hits += glob.glob(os.path.join(root, "AutoCAD *", "accoreconsole.exe"))
    if not hits:
        return None
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


# ── MTAP_WINDOW rectangle pre-measured from MTAP_TEMPLATE.dxf ────────────────
# The LWPOLYLINE on layer MTAP_WINDOW in the template block defines the area
# reserved for the drill drawing.  Scale + placement of the template is computed
# so the drill drawing fills MARGIN fraction of this window.
_TPL_WIN_W  = 712.149    # window width  in template local coords (mm)
_TPL_WIN_H  = 404.821    # window height in template local coords (mm)
_TPL_WIN_CX = 387.559    # window center X
_TPL_WIN_CY = 267.065    # window center Y
_TPL_MARGIN = 0.70       # drawing fills 70 % of the window (matches LISP)


def _dxf_blocks_dir() -> str | None:
    """Return the blocks dir if all DXF block files exist, else None."""
    bd = resource_path(os.path.join("autocad", "blocks"))
    needed = ("MTAP_GDT.dxf", "MTAP_DATUM.dxf", "MTAP_TEMPLATE.dxf")
    if all(os.path.isfile(os.path.join(bd, f)) for f in needed):
        return bd
    return None


def finalize_with_ezdxf(geom_dxf: str, p: DrillProposalParams,
                         anchors: dict, out_path: str) -> None:
    """Insert blocks and template using pure ezdxf — no AutoCAD required.

    Imports each DXF block file's model-space content as a named block via
    ezdxf's xref.Loader, inserts the blocks at the computed anchors, scales
    and places MTAP_TEMPLATE around the drawing, then saves.
    """
    from ezdxf import xref, bbox as _bbox

    blocks_dir = LspWriter._resolve_blocks_dir(None)

    doc = ezdxf.readfile(geom_dxf)
    msp = doc.modelspace()

    def _import_as_block(dxf_path: str, block_name: str) -> None:
        if block_name in doc.blocks:
            return
        src = ezdxf.readfile(dxf_path)
        # Move model-space entities into a named block within the source doc
        src_blk = src.blocks.new(block_name)
        for entity in list(src.modelspace()):
            src.modelspace().move_to_layout(entity, src_blk)
        # Import the named block (and all its sub-block dependencies) into doc
        loader = xref.Loader(src, doc, conflict_policy=xref.ConflictPolicy.KEEP)
        loader.load_block_layout(src_blk)
        for blk in src.blocks:
            if not blk.name.startswith("*") and blk.name != block_name:
                loader.load_block_layout(blk)
        loader.execute()

    _import_as_block(os.path.join(blocks_dir, "MTAP_GDT.dxf"),      "MTAP_GDT")
    _import_as_block(os.path.join(blocks_dir, "MTAP_DATUM.dxf"),     "MTAP_DATUM")
    _import_as_block(os.path.join(blocks_dir, "MTAP_TEMPLATE.dxf"),  "MTAP_TEMPLATE")

    # Ensure annotation layer
    if "MTAP-GDT" not in doc.layers:
        doc.layers.new("MTAP-GDT", dxfattribs={"color": 2})

    # Scale factors (same formula as LISP)
    feature   = max(p.overall_length, p.cutting_diameter * 4.0,
                    p.effective_shank_diameter * 4.0, 1.0)
    block_txt = feature * 0.0154 * _BLOCK_TXT_RATIO
    scale_gdt = block_txt / _GDT_REF_H
    scale_dat = block_txt / _DAT_REF_H

    if p.runout > 0:
        gx, gy = anchors["gdt_ins"]
        dx, dy = anchors["dat_ins"]
        gdt_ref = msp.add_blockref(
            "MTAP_GDT", (gx, gy),
            dxfattribs={"layer": "MTAP-GDT", "xscale": scale_gdt, "yscale": scale_gdt},
        )
        gdt_ref.add_auto_attribs({"VAL": f"{p.runout:.3f}", "A": "A"})
        dat_ref = msp.add_blockref(
            "MTAP_DATUM", (dx, dy),
            dxfattribs={"layer": "MTAP-GDT", "xscale": scale_dat, "yscale": scale_dat},
        )
        dat_ref.add_auto_attribs({"-A-": "A"})

    # Compute bounding box of all existing geometry for template placement
    bb = _bbox.extents(msp, fast=True)
    if bb.is_empty:
        extmin = (0.0, -p.cutting_diameter / 2)
        extmax = (p.overall_length, p.cutting_diameter / 2)
    else:
        extmin = (bb.extmin.x, bb.extmin.y)
        extmax = (bb.extmax.x, bb.extmax.y)

    tw  = extmax[0] - extmin[0]
    th  = extmax[1] - extmin[1]
    tcx = (extmin[0] + extmax[0]) / 2.0
    tcy = (extmin[1] + extmax[1]) / 2.0

    s   = max(tw / (_TPL_WIN_W * _TPL_MARGIN),
              th / (_TPL_WIN_H * _TPL_MARGIN), 1e-6)
    ipx = tcx - s * _TPL_WIN_CX
    ipy = tcy - s * _TPL_WIN_CY

    msp.add_blockref("MTAP_TEMPLATE", (ipx, ipy), dxfattribs={"xscale": s, "yscale": s})

    doc.header["$DIMLFAC"] = 1.0

    doc.saveas(out_path)
    log.info("finalize_with_ezdxf: saved %s  (template scale=%.4f)", out_path, s)

    _verify_output(out_path, require_gdt=(p.runout > 0))


def _accoreconsole_dxfout(acc: str, in_dxf: str, out_path: str) -> None:
    """Open in_dxf in accoreconsole and DXFOUT to out_path.

    This is the step that makes the file open read-WRITE in AutoCAD.
    ezdxf-authored DXFs are always read-only; only AutoCAD's own DXFOUT
    produces a file AutoCAD considers native (and therefore editable).
    """
    import tempfile as _tmp
    scr_fd, scr_path = _tmp.mkstemp(suffix=".scr")
    try:
        out_fwd = _lsp_path(os.path.abspath(out_path)).replace('"', '\\"')
        with os.fdopen(scr_fd, "w", encoding="utf-8") as fh:
            fh.write("FILEDIA 0\nCMDDIA 0\n")
            fh.write(f'DXFOUT "{out_fwd}" V 2010 16\n')
            fh.write("QUIT Y\n")

        if os.path.exists(out_path):
            os.remove(out_path)

        res = subprocess.run(
            [acc, "/i", in_dxf, "/s", scr_path],
            capture_output=True, timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if not os.path.isfile(out_path):
            raise RuntimeError(
                f"accoreconsole DXFOUT produced no output (exit {res.returncode}).\n"
                f"stderr: {(res.stderr or b'').decode('latin-1', errors='replace')[:500]}")
        log.info("_accoreconsole_dxfout: OK → %s", out_path)
    finally:
        try:
            os.unlink(scr_path)
        except OSError:
            pass


def finalize_with_acad(geom_dxf: str, p: DrillProposalParams,
                       anchors: dict, out_path: str,
                       require_acad: bool = False) -> None:
    """Insert blocks/template and write the final DXF.

    Prefers the pure-ezdxf path when DXF block files are present (fast, no
    AutoCAD required).  Falls back to accoreconsole when only DWG blocks are
    available.

    Args:
        require_acad: if True, skip the ezdxf path and require accoreconsole
            (use in batch/CLI mode to guarantee AutoCAD-authored output).
    """
    # Fast path: DXF blocks available — use ezdxf for block insertion.
    # If accoreconsole is also present, pipe the result through DXFOUT so the
    # file opens read-WRITE in AutoCAD (ezdxf-authored DXFs are always read-only).
    if _dxf_blocks_dir() is not None:
        acc_early = _find_accoreconsole()
        if acc_early:
            log.info("finalize_with_acad: DXF blocks + accoreconsole — ezdxf prep then DXFOUT.")
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".dxf", prefix="mtap_ezdxf_")
            os.close(tmp_fd)
            try:
                finalize_with_ezdxf(geom_dxf, p, anchors, tmp_path)
                _accoreconsole_dxfout(acc_early, tmp_path, out_path)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        else:
            log.warning("finalize_with_acad: no accoreconsole — DXF will open read-only in AutoCAD.")
            finalize_with_ezdxf(geom_dxf, p, anchors, out_path)
        return

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
            creationflags=subprocess.CREATE_NO_WINDOW,
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
