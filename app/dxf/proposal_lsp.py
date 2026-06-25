"""
proposal_lsp.py — DMTAP direct-draw for the Proposal (drill) drawing.

Mirrors what the Blank screen does, but for the full proposal drill: instead of
writing a DXF (which AutoCAD then has to open — the path that was crashing), this
emits a self-contained AutoLISP link file that the DMTAP command draws directly
as native AutoCAD entities.

Pipeline
--------
1. Build the OCC solid (cached) and tessellate it.
2. Project visible edges with the Node.js helper -> side-view segments
   (identical geometry to the DXF side view).
3. Compute the analytical end view, centerlines, dimensions and GD&T anchors
   in Python.
4. Emit:  shared _LIBRARY  +  (setq ...) data  +  a proposal-specific
   (defun MTAP:draw ...) that OVERRIDES the Blank's draw  +  (MTAP:draw).

The shared library (layers, block load/insert, template wrap+fill, dim vars) is
reused verbatim from lsp_writer, so block scaling and the title-block template
behave exactly like the Blank drawing.  Blocks insert at
    scale = (dim_text_height * _BLOCK_TXT_RATIO) / block_reference_height
which is why the blocks are no longer tiny.
"""

import math
import os

from app.engine.tools.drill import DrillProposalParams
from app.dxf.lsp_writer import (
    _LIBRARY, LspWriter, _num, _pt, _bool, _lstr, _lsp_path,
    _BLOCK_TXT_RATIO, _GDT_REF_H, _DAT_REF_H,
)
from app.utils.logging_setup import get_logger

log = get_logger()


# ══════════════════════════════════════════════════════════════════════════════
#  Proposal-specific draw routine (OVERRIDES the Blank's MTAP:draw)
# ══════════════════════════════════════════════════════════════════════════════
_PROPOSAL_DRAW = r"""
;; ── PROPOSAL draw (overrides the Blank MTAP:draw defined above) ───────────────
(defun MTAP:draw ( / osm cme res blk)
  (setq osm (getvar "OSMODE") cme (getvar "CMDECHO"))
  (setvar "OSMODE" 0) (setvar "CMDECHO" 0)

  (setq res
    (vl-catch-all-apply
      (function (lambda ()

        ;; layers (2=yellow 4=cyan 1=red)
        (MTAP:make-layer "MTAP-OUTLINE" 2 "Continuous")
        (MTAP:make-layer "MTAP-FRONT"   2 "Continuous")
        (MTAP:make-layer "MTAP-CENTER"  4 "CENTER")
        (MTAP:make-layer "MTAP-DIM"     1 "Continuous")
        (MTAP:make-layer "MTAP-ANNOT"   2 "Continuous")
        (MTAP:make-style)
        (MTAP:setvars)

        (princ (strcat "\n=== MTAP PROPOSAL ==="
                       "\n  scales: GDT=" (rtos MTAP:SCALE_GDT 2 2)
                       "  DAT="           (rtos MTAP:SCALE_DAT 2 2)
                       "\n  side segs="   (itoa (length MTAP:SIDE)) "\n"))

        ;; force-redefine the blocks we use (every run, from the extracted DWGs)
        (MTAP:load-block "MTAP_GDT"      MTAP:PATH_GDT)
        (MTAP:load-block "MTAP_DATUM"    MTAP:PATH_DATUM)
        (MTAP:load-block "MTAP_TEMPLATE" MTAP:PATH_TEMPLATE)

        ;; mark the start so the template can wrap everything drawn after here
        (setq MTAP:TOOLSTART (entlast))

        ;; ── side view: many short visible-edge segments ──────────────────────
        (setvar "CLAYER" "MTAP-OUTLINE")
        (foreach sg MTAP:SIDE
          (command "_.LINE" (car sg) (cadr sg) ""))

        ;; ── end view: outer circle + cutting lips (+ web circle for >2 flutes)─
        (setvar "CLAYER" "MTAP-FRONT")
        (command "_.CIRCLE" MTAP:ENDC MTAP:ENDR)
        (foreach ln MTAP:ENDLINES
          (command "_.LINE" (car ln) (cadr ln) ""))
        (if MTAP:ENDWEB
          (command "_.CIRCLE" MTAP:ENDC MTAP:ENDWEBR))

        ;; ── centerlines: axis + end-view cross ───────────────────────────────
        (setvar "CLAYER" "MTAP-CENTER")
        (command "_.LINE" MTAP:CL1   MTAP:CL2   "") (MTAP:set-center)
        (command "_.LINE" MTAP:ECLH1 MTAP:ECLH2 "") (MTAP:set-center)
        (command "_.LINE" MTAP:ECLV1 MTAP:ECLV2 "") (MTAP:set-center)

        ;; ── dimensions ───────────────────────────────────────────────────────
        (setvar "CLAYER" "MTAP-DIM")
        ;; Dc (vertical, left of the tip)
        (command "_.DIMLINEAR"
                 (list MTAP:XPB MTAP:RC) (list MTAP:XPB (- MTAP:RC))
                 "_Text" (strcat "%%c" MTAP:DCSTR) (list MTAP:DCDIMX 0.0))
        ;; D (vertical, right of the back face) only if it differs
        (if MTAP:HASD
          (command "_.DIMLINEAR"
                   (list MTAP:XEND MTAP:RS) (list MTAP:XEND (- MTAP:RS))
                   "_Text" (strcat "%%c" MTAP:DSTR) (list MTAP:DDIMX 0.0)))
        ;; OAL + shank length (horizontal, below)
        (command "_.DIMLINEAR" MTAP:OAL1 MTAP:OAL2 "_Horizontal" MTAP:OALLOC)
        (command "_.DIMLINEAR" MTAP:LS1  MTAP:LS2  "_Horizontal" MTAP:LSLOC)
        ;; point angle (angular, at the tip)
        (if MTAP:HASPT
          (command "_.DIMANGULAR" "" MTAP:APEX MTAP:PA1 MTAP:PA2 MTAP:PALOC))

        ;; ── GD&T runout frame + datum (only when runout > 0) ─────────────────
        (if MTAP:HASGDT
          (progn
            (setvar "CLAYER" "MTAP-DIM")
            (setq blk (MTAP:ins-block "MTAP_GDT" MTAP:GDTINS MTAP:SCALE_GDT))
            (MTAP:set-attrib blk "VAL" MTAP:GDTVAL)
            (command "_.LINE" MTAP:GDT_LDR1 MTAP:GDT_LDR2 "")
            (MTAP:ins-block "MTAP_DATUM" MTAP:DATINS MTAP:SCALE_DAT)))

        ;; ── customer template: border + title block, scaled to wrap drawing ──
        (setq blk (MTAP:place-template (MTAP:range-bbox MTAP:TOOLSTART)))
        (MTAP:fill-template blk)

        (command "_.ZOOM" "_Extents")
        "ok"))))

  (setvar "OSMODE" osm) (setvar "CMDECHO" cme)
  (if (vl-catch-all-error-p res)
    (progn (princ "\n*** MTAP ERROR: ")
           (princ (vl-catch-all-error-message res))
           (princ "\n    Please report this message."))
    (princ "\nMTAP: proposal drawing complete."))
  (princ))
;; end proposal draw
"""


# ══════════════════════════════════════════════════════════════════════════════
#  Geometry helpers (pure Python — mirror proposal_dxf's analytical views)
# ══════════════════════════════════════════════════════════════════════════════

def _end_view_lines(p: DrillProposalParams, rc: float, front_cx: float):
    """Return (lines, web_radius_or_None) for the analytical end view.

    `lines` is a list of ((x1,y1),(x2,y2)) cutting-lip / chisel segments.
    Mirrors proposal_dxf._add_end_view exactly so DMTAP draws the same view.
    """
    cx, cy = front_cx, 0.0
    web_r  = rc * 0.15

    lip_ang = math.radians(90.0 - p.helix_angle)
    chi_ang = lip_ang + math.pi / 2.0
    cos_l, sin_l = math.cos(lip_ang), math.sin(lip_ang)
    cos_c, sin_c = math.cos(chi_ang), math.sin(chi_ang)

    lines: list[tuple] = []
    if p.n_flutes == 2:
        p1 = (cx + rc    * cos_l, cy + rc    * sin_l)
        p2 = (cx - rc    * cos_l, cy - rc    * sin_l)
        b1 = (cx + web_r * cos_c, cy + web_r * sin_c)
        b2 = (cx - web_r * cos_c, cy - web_r * sin_c)
        lines = [(b1, p1), (b2, p2), (b1, b2)]
        return lines, None
    else:
        for i in range(p.n_flutes):
            a = lip_ang + 2.0 * math.pi * i / p.n_flutes
            outer = (cx + rc    * math.cos(a),          cy + rc    * math.sin(a))
            inner = (cx + web_r * math.cos(a + math.pi), cy + web_r * math.sin(a + math.pi))
            lines.append((inner, outer))
        return lines, web_r


def _segs_lsp(segs) -> str:
    """LISP list of 2-point segments: (list (list p1 p2) (list p1 p2) ...)."""
    parts = [f"(list {_pt(a[0], a[1])} {_pt(b[0], b[1])})" for (a, b) in segs]
    return "(list " + " ".join(parts) + ")" if parts else "(list)"


# ══════════════════════════════════════════════════════════════════════════════
#  Link-file text builder
# ══════════════════════════════════════════════════════════════════════════════

def _build_link_text(p: DrillProposalParams, side_segs, blocks_dir: str,
                     meta: dict | None = None,
                     end_segs=None) -> str:
    """Assemble the full mtap_link.lsp text for the proposal drill."""
    meta = meta or {}
    lines: list[str] = [_LIBRARY]
    a = lines.append

    rc   = p.cutting_diameter / 2.0
    rs   = p.effective_shank_diameter / 2.0
    rmax = max(rc, rs)

    feature = max(p.overall_length, p.cutting_diameter * 4.0,
                  p.effective_shank_diameter * 4.0, 1.0)
    h   = max(feature * 0.018, 0.8)          # dim text height (matches proposal DXF)
    txt = h

    front_r  = max(rc, rs)
    front_cx = p.overall_length + front_r * 2.5
    pad      = rc * 0.5
    ext      = front_r + pad

    # ── block paths ───────────────────────────────────────────────────────────
    bd = _lsp_path(blocks_dir) if blocks_dir else "."
    a(";; block paths — extracted from the exe at link-generation time")
    a(f'(setq MTAP:PATH_GDT      "{bd}/MTAP_GDT.dwg")')
    a(f'(setq MTAP:PATH_DATUM    "{bd}/MTAP_DATUM.dwg")')
    a(f'(setq MTAP:PATH_TEMPLATE "{bd}/MTAP_TEMPLATE.dwg")')
    a("")

    # ── title-block metadata ──────────────────────────────────────────────────
    title = (f"DRILL  Dc{p.cutting_diameter:g} x D{p.effective_shank_diameter:g}"
             f" x OAL{p.overall_length:g}  {p.n_flutes}FL")
    a(";; drawing metadata — feeds the template title block")
    a(f"(setq MTAP:CUSTOMER  {_lstr(meta.get('customer', ''))})")
    a(f"(setq MTAP:DRAWNBY   {_lstr(meta.get('drawn_by', ''))})")
    a(f"(setq MTAP:CHECKEDBY {_lstr(meta.get('checked_by', ''))})")
    a(f"(setq MTAP:DESC      {_lstr(meta.get('description', ''))})")
    a(f"(setq MTAP:TITLE     {_lstr(meta.get('title') or title)})")
    a(f"(setq MTAP:DATE      {_lstr(meta.get('date', ''))})")
    a("")

    # ── text height + block insertion scales ──────────────────────────────────
    block_txt = txt * _BLOCK_TXT_RATIO
    a(f";; block text = dim text * {_BLOCK_TXT_RATIO} = {block_txt:.3f} mm")
    a(f"(setq MTAP:TXT     {_num(txt)})")
    a(f"(setq MTAP:LTSCALE {_num(max(rmax * 0.6, 0.5))})")
    a(f"(setq MTAP:SCALE_GDT {_num(block_txt / _GDT_REF_H)})")
    a(f"(setq MTAP:SCALE_DAT {_num(block_txt / _DAT_REF_H)})")
    a("")

    # ── side view segments ────────────────────────────────────────────────────
    a(f";; side view — {len(side_segs)} visible-edge segments")
    a(f"(setq MTAP:SIDE {_segs_lsp(side_segs)})")
    a("")

    # ── end view ──────────────────────────────────────────────────────────────
    a(f"(setq MTAP:ENDC {_pt(front_cx, 0.0)})")
    a(f"(setq MTAP:ENDR {_num(rc)})")
    if end_segs is not None:
        # HLR-projected cutting lips from 3D solid (x1,y1,x2,y2 flat tuples)
        hlr_lines = [((x1, y1), (x2, y2)) for (x1, y1, x2, y2) in end_segs]
        a(f"(setq MTAP:ENDLINES {_segs_lsp(hlr_lines)})")
        a(f"(setq MTAP:ENDWEB nil)")
        a(f"(setq MTAP:ENDWEBR nil)")
    else:
        end_lines, web_r = _end_view_lines(p, rc, front_cx)
        a(f"(setq MTAP:ENDLINES {_segs_lsp(end_lines)})")
        a(f"(setq MTAP:ENDWEB {_bool(web_r is not None)})")
        a(f"(setq MTAP:ENDWEBR {_num(web_r) if web_r is not None else 'nil'})")
    a("")

    # ── centerlines ───────────────────────────────────────────────────────────
    a(f"(setq MTAP:CL1 {_pt(-pad, 0.0)})")
    a(f"(setq MTAP:CL2 {_pt(p.overall_length + pad, 0.0)})")
    a(f"(setq MTAP:ECLH1 {_pt(front_cx - ext, 0.0)})")
    a(f"(setq MTAP:ECLH2 {_pt(front_cx + ext, 0.0)})")
    a(f"(setq MTAP:ECLV1 {_pt(front_cx, -ext)})")
    a(f"(setq MTAP:ECLV2 {_pt(front_cx,  ext)})")
    a("")

    # ── diameter dims ─────────────────────────────────────────────────────────
    a(f"(setq MTAP:XPB    {_num(p.x_point_base)})")
    a(f"(setq MTAP:RC     {_num(rc)})")
    a(f"(setq MTAP:DCDIMX {_num(-(rc + h * 6.0))})")
    a(f'(setq MTAP:DCSTR  "{p.cutting_diameter:g}")')
    has_d = abs(rs - rc) > 1e-3
    a(f"(setq MTAP:HASD   {_bool(has_d)})")
    a(f"(setq MTAP:XEND   {_num(p.x_end)})")
    a(f"(setq MTAP:RS     {_num(rs)})")
    a(f"(setq MTAP:DDIMX  {_num(p.x_end + rs + h * 6.0)})")
    a(f'(setq MTAP:DSTR   "{p.effective_shank_diameter:g}")')
    a("")

    # ── length dims (horizontal, below the part) ──────────────────────────────
    oal_y = -(rmax + h * 9.0)
    ls_y  = -(rmax + h * 5.0)
    a(f"(setq MTAP:OAL1 {_pt(0.0, 0.0)})")
    a(f"(setq MTAP:OAL2 {_pt(p.overall_length, 0.0)})")
    a(f"(setq MTAP:OALLOC {_pt(p.overall_length / 2.0, oal_y)})")
    a(f"(setq MTAP:LS1 {_pt(p.x_shank_start, -rs)})")
    a(f"(setq MTAP:LS2 {_pt(p.x_end, -rs)})")
    a(f"(setq MTAP:LSLOC {_pt((p.x_shank_start + p.x_end) / 2.0, ls_y)})")
    a("")

    # ── point angle ───────────────────────────────────────────────────────────
    has_pt = p.point_length > 1e-6
    a(f"(setq MTAP:HASPT {_bool(has_pt)})")
    if has_pt:
        pa_loc_x = max(rc * 1.5, p.point_length * 0.5)
        a(f"(setq MTAP:APEX  {_pt(0.0, 0.0)})")
        a(f"(setq MTAP:PA1   {_pt(p.x_point_base, -rc)})")
        a(f"(setq MTAP:PA2   {_pt(p.x_point_base,  rc)})")
        a(f"(setq MTAP:PALOC {_pt(pa_loc_x, 0.0)})")
    else:
        a("(setq MTAP:APEX nil MTAP:PA1 nil MTAP:PA2 nil MTAP:PALOC nil)")
    a("")

    # ── GD&T runout frame + datum ─────────────────────────────────────────────
    has_gdt = p.runout > 0
    a(f"(setq MTAP:HASGDT {_bool(has_gdt)})")
    if has_gdt:
        gdt_x = p.x_point_base
        gdt_y = rc + h * 6.0
        dat_x = p.x_shank_start + p.shank_length * 0.6
        a(f"(setq MTAP:GDTINS   {_pt(gdt_x, gdt_y)})")
        a(f'(setq MTAP:GDTVAL   "{p.runout:.3f}")')
        a(f"(setq MTAP:GDT_LDR1 {_pt(gdt_x, gdt_y)})")   # frame corner
        a(f"(setq MTAP:GDT_LDR2 {_pt(gdt_x, rc)})")      # down onto the part top
        a(f"(setq MTAP:DATINS   {_pt(dat_x, rs)})")      # datum on the shank
    else:
        for v in ("MTAP:GDTINS", "MTAP:GDTVAL", "MTAP:GDT_LDR1",
                  "MTAP:GDT_LDR2", "MTAP:DATINS"):
            a(f"(setq {v} nil)")
    a("")

    # ── proposal draw override + auto-execute ─────────────────────────────────
    a(_PROPOSAL_DRAW)
    a("(MTAP:draw)")
    a("(princ)")
    a("")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def generate_proposal_link(p: DrillProposalParams, link_path: str, *,
                           meta: dict | None = None, progress=None) -> None:
    """Build the drill geometry and write the DMTAP link file at `link_path`.

    Heavy work (solid build + tessellation + edge projection) runs here, so call
    this from a worker thread.  After it returns the user types DMTAP in AutoCAD.
    """
    def _p(pct, msg):
        if progress:
            progress(pct, msg)

    errs = p.validate()
    if errs:
        raise ValueError("\n".join(errs))

    # Lazy import to avoid pulling OCC into the GUI process at startup.
    from app.dxf.proposal_dxf import (
        _build_solid_cached, _project_via_hlr, _project_via_hlr_end, _heal_segs,
    )

    _p(5, "Building solid…")
    solid = _build_solid_cached(p, _progress=progress, _base_pct=5, _end_pct=60)

    rc = p.cutting_diameter / 2.0
    front_cx = p.overall_length + rc * 2 * 2.5

    _p(70, "HLR projection…")
    all_segs = _project_via_hlr(solid)
    all_segs = _heal_segs(all_segs)
    segs = [((z1, x1), (z2, x2)) for (z1, x1, z2, x2) in all_segs]

    _p(82, "HLR end view…")
    end_raw = _project_via_hlr_end(solid, rc, cx=front_cx, cy=0.0)
    end_segs = _heal_segs(end_raw)

    _p(88, "Writing AutoCAD link…")
    os.makedirs(os.path.dirname(link_path), exist_ok=True)
    blocks_dir = LspWriter._resolve_blocks_dir(os.path.dirname(link_path))
    text = _build_link_text(p, segs, blocks_dir, meta, end_segs=end_segs)
    with open(link_path, "w", encoding="utf-8") as fh:
        fh.write(text)

    ok = os.path.exists(link_path) and os.path.getsize(link_path) > 0
    log.info("Proposal AutoCAD link %s (HLR %d segs, %d bytes) -> %s",
             "OK" if ok else "FAILED", len(segs),
             os.path.getsize(link_path) if ok else 0, link_path)
    _p(100, "AutoCAD link ready — type DMTAP")
