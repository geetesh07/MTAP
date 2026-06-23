"""
proposal_dxf.py — Proposal Drawing DXF generator (production drawing).

Pipeline:
  1. Build drill solid as a REVOLVED 2D profile (tip cone + Dc body +
     reinforcement cone/step + shank + back-face chamfer), then cut helical
     flutes.  Revolving a lathe profile gives every transition for free.
  2. Tessellate solid -> triangle mesh JSON.
  3. nodejs/gen_proposal.mjs runs three-edge-projection twice:
       side view  (project along Y)  and  front/end view (project along Z).
  4. ezdxf writes a production drawing: both views, centerlines, linear +
     angular dimensions, a circular-runout feature control frame + datum,
     and a title block / border.
"""

import json
import math
import os
import subprocess
import tempfile
import time

import ezdxf

from app.engine.tools.drill import DrillProposalParams
from app.utils.config import resource_path

EPS = 1e-6
# Spine point density. 16/turn gives a smooth interpolated helix for the 3D
# solid + STEP.  Do NOT raise toward 24: on small-diameter / low-helix drills a
# dense spine degenerates the swept-pipe surface and the flute Boolean explodes
# from ~3 s to ~80 s.  The DXF side view is drawn analytically (not from the
# mesh), so it needs no extra spine density.
_HELIX_PTS_PER_TURN = 16
_HELIX_MIN_PTS      = 10          # minimum spine points per flute (floor for short bodies)
_MESH_DEFLECTION    = 0.12        # mm — coarser than 0.05; fine for DXF projection

# ── flute geometry rules (per the tool-design spec) ───────────────────────────
# Core diameter = the web left between flutes after the helix is cut, as a
# fraction of Dc.  Deeper flute => smaller core.  flute_r = rc * (1 - core_frac),
# so the cutter (centred on the Dc surface) penetrates to core_radius = core*rc.
_CORE_DIA_FRAC = {2: 0.33, 3: 0.45, 4: 0.55}
_CORE_DIA_DEFAULT = 0.45

_TIP_OVERSHOOT_FRAC = 0.10        # helix starts 10% of point_length PAST the apex
_SWAP_LEN_FRAC      = 1.3         # flute run-out ("swap") length = Dc * 1.3
_SWAP_ANGLE_DEG     = 35.0        # run-out lifts radially out of the body at 35deg

# ── solid cache ───────────────────────────────────────────────────────────────
# Holds the last successfully-built OCC solid so repeat DXF/STEP requests with
# the same parameters skip the 4-second OCC build entirely.
# Format: (key_tuple, helix_pts, helix_min, solid_shape)
# Only one entry — single-session, no memory growth concern.
_solid_cache: tuple | None = None


def _make_params_key(p: "DrillProposalParams") -> tuple:
    """Hashable key for a DrillProposalParams + current quality constants."""
    return (
        round(p.cutting_diameter,    6),
        round(p.shank_diameter,      6),
        round(p.overall_length,      6),
        round(p.shank_length,        6),
        round(p.point_angle,         6),
        round(p.helix_angle,         6),
        p.n_flutes,
        p.reinforcement,
        round(p.reinforcement_angle, 6),
        round(p.runout,              6),
        _HELIX_PTS_PER_TURN,   # quality matters — preview solid ≠ DXF solid
        _HELIX_MIN_PTS,
    )


def _build_solid_cached(p: "DrillProposalParams", *,
                        _progress=None, _base_pct: int = 5, _end_pct: int = 58):
    """Build or return a cached OCC solid for p at the current quality settings.

    Cache hit saves ~4 s on repeat DXF/STEP requests with identical parameters.
    Thread-safe because the UI disables all buttons while any worker is running
    (only one worker thread can call this at a time).
    """
    global _solid_cache
    key = _make_params_key(p)
    if _solid_cache is not None and _solid_cache[0] == key:
        if _progress:
            _progress(_base_pct + (_end_pct - _base_pct) // 2,
                      "Using cached solid (params unchanged)…")
            _progress(_end_pct, "Cached solid ready.")
        return _solid_cache[1]

    solid = _build_solid(p, _progress=_progress,
                         _base_pct=_base_pct, _end_pct=_end_pct)
    _solid_cache = (key, solid)
    return solid

def _node_script() -> str:
    return resource_path(os.path.join("nodejs", "gen_proposal.mjs"))


# ══════════════════════════════════════════════════════════ solid construction ══

def _make_flute_spine(z_tip, z_swap_start, z_swap_end, radius,
                      helix_angle_deg, phase_rad, flute_r):
    """One continuous flute spine: constant-radius helix, then a run-out ("swap")
    that spirals smoothly OUT of the body.  Returned as a single B-spline wire so
    MakePipe sees one smooth curve — the swap is genuinely part of the helix.

    Geometry
    --------
    angle(z) = w·(z - z_tip) + phase   — the cutter keeps the SAME helical lead
    for the whole spine (helix and swap), so the run-out is a continuation of the
    twist, not a separate feature.

    radius(z):
      • z ≤ z_swap_start            → rc                 (cuts the flute)
      • z_swap_start … z_swap_end   → rc → r_exit via SMOOTHSTEP (3t²−2t³)

    Why smoothstep and not a straight ramp: smoothstep has ZERO slope at both
    ends.  At the junction its slope matches the flat helix (dr/dz = 0), so the
    spine has no corner there — a single B-spline through it stays smooth instead
    of overshooting into a banana.  A linear ramp injects a slope discontinuity
    that the global interpolation rings on; that was the old failure.

    r_exit = rc + flute_r + margin → the cutter disc (radius flute_r) fully clears
    the body surface (radius rc) by z_swap_end, so the flute "runs completely off
    the body" exactly at the end of the swap length.  The spine STOPS at
    z_swap_end, so there is no wasted tube beyond the body to slow the Boolean.
    """
    ha     = math.radians(helix_angle_deg)
    pitch  = math.pi * 2.0 * radius / math.tan(ha)
    w      = 2.0 * math.pi / pitch                 # rad of twist per mm of z

    r_exit = radius + flute_r + 0.5                # clear the body + 0.5 mm margin
    swap   = max(z_swap_end - z_swap_start, 0.0)

    def angle_at(z):
        return w * (z - z_tip) + phase_rad

    pts = []

    # ── helix segment: z_tip → z_swap_start, constant radius ──────────────────
    helix_len   = max(z_swap_start - z_tip, 1e-6)
    helix_turns = helix_len / pitch
    n1          = max(_HELIX_MIN_PTS, int(_HELIX_PTS_PER_TURN * helix_turns))
    for i in range(n1 + 1):                         # include the junction point
        z = z_tip + helix_len * i / n1
        a = angle_at(z)
        pts.append((radius * math.cos(a), radius * math.sin(a), z))

    # ── swap segment: z_swap_start → z_swap_end, smoothstep radius ────────────
    # Sampled with FEW points (8): the swap curve is gentle, and a low point
    # count keeps the resulting B-spline pipe surface simple.  This is critical
    # for Boolean speed — a dense (20-pt) spine makes the tangent run-out cut
    # take 15 s; 8 pts cuts it to <1 s.  Starts at j=1 so the junction point
    # isn't duplicated.
    if swap > EPS:
        n2 = 6
        for j in range(1, n2 + 1):
            t = j / n2
            s = t * t * (3.0 - 2.0 * t)             # smoothstep
            z = z_swap_start + swap * t
            r = radius + (r_exit - radius) * s
            a = angle_at(z)
            pts.append((r * math.cos(a), r * math.sin(a), z))

    arr = TColgp_HArray1OfPnt(1, len(pts))
    for k, (x, y, z) in enumerate(pts):
        arr.SetValue(k + 1, gp_Pnt(x, y, z))

    interp = GeomAPI_Interpolate(arr, False, 1e-4)
    interp.Perform()
    edge = BRepBuilderAPI_MakeEdge(interp.Curve()).Edge()
    return BRepBuilderAPI_MakeWire(edge).Wire()





def _disk_profile(center, normal_dir, radius):
    from OCP.GC import GC_MakeCircle
    ax   = gp_Ax2(center, normal_dir)
    circ = GC_MakeCircle(ax, radius).Value()
    edge = BRepBuilderAPI_MakeEdge(circ).Edge()
    wire = BRepBuilderAPI_MakeWire(edge).Wire()
    return BRepBuilderAPI_MakeFace(wire).Face()


def _revolve_profile(rz_points):
    """Revolve a closed (r,z) lathe profile 360deg about the Z axis -> solid."""
    wire = BRepBuilderAPI_MakeWire()
    n = len(rz_points)
    for i in range(n):
        r1, z1 = rz_points[i]
        r2, z2 = rz_points[(i + 1) % n]     # wrap to close the loop
        e = BRepBuilderAPI_MakeEdge(gp_Pnt(r1, 0, z1), gp_Pnt(r2, 0, z2)).Edge()
        wire.Add(e)
    face = BRepBuilderAPI_MakeFace(wire.Wire(), True).Face()
    axis = gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    rev  = BRepPrimAPI_MakeRevol(face, axis)
    rev.Build()
    return rev.Shape()


def _profile_points(p, rc, rs, chamfer):
    """
    Closed lathe profile (radius, axial-z) traversed from the tip-axis point,
    out along the cutting edge, down the body/reinforcement/shank, across the
    back-face chamfer, and back to the axis.  The closing edge (back-axis ->
    tip-axis) lies on the centerline.
    """
    pts = [(0.0, 0.0)]                                   # tip on axis

    if p.point_length > EPS:
        pts.append((rc, p.x_point_base))                # cone edge to body
    else:
        pts.append((rc, 0.0))                           # flat tip face corner

    pts.append((rc, p.x_body_end))                      # end of Dc body

    if p.has_transition_cone:
        pts.append((rs, p.x_shank_start))               # sloped reinforcement
    elif abs(rs - rc) > EPS:
        pts.append((rs, p.x_body_end))                  # abrupt step to shank

    if chamfer > EPS:
        pts.append((rs, p.x_end - chamfer))             # shank up to chamfer
        pts.append((rs - chamfer, p.x_end))             # 45deg back chamfer
    else:
        pts.append((rs, p.x_end))                       # square back corner

    pts.append((0.0, p.x_end))                          # back-face on axis

    # drop consecutive duplicates (zero-length sections)
    out = []
    for q in pts:
        if not out or abs(q[0] - out[-1][0]) > EPS or abs(q[1] - out[-1][1]) > EPS:
            out.append(q)
    return out


def _build_solid(p: DrillProposalParams, *,
                 _progress=None, _base_pct: int = 5, _end_pct: int = 58):
    """Build the revolve-then-flute-cut solid.

    Progress is reported between _base_pct and _end_pct (inclusive).
    _progress(percent, message) is called at each stage if provided.
    """
    def _p(pct, msg):
        if _progress:
            _progress(pct, msg)

    # OCP imports are lazy so the GUI can open without loading OpenCASCADE.
    # All OCP symbols used anywhere in this file are imported here once; the
    # module-level helper functions (_make_helix_wire, _disk_profile, etc.) use
    # these names via the module globals dict after this function sets them.
    import sys as _sys
    _g = _sys.modules[__name__].__dict__
    if "gp_Pnt" not in _g:
        from OCP.gp import gp_Pnt, gp_Dir, gp_Ax1, gp_Ax2, gp_Vec, gp_Trsf
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeRevol, BRepPrimAPI_MakePrism
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.BRep import BRep_Tool, BRep_Builder
        from OCP.TopLoc import TopLoc_Location
        from OCP.GeomAPI import GeomAPI_Interpolate
        from OCP.TColgp import TColgp_HArray1OfPnt
        from OCP.gp import gp_Vec
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
        from OCP.TopoDS import TopoDS, TopoDS_Compound
        _g.update({k: v for k, v in locals().items() if not k.startswith("_")})

    # Re-bind to locals unconditionally — Python marks any name that appears in
    # an assignment anywhere in the function as a local for the ENTIRE function.
    # Without this the second call skips the if-block and hits UnboundLocalError.
    gp_Pnt                   = _g["gp_Pnt"]
    gp_Dir                   = _g["gp_Dir"]
    gp_Vec                   = _g["gp_Vec"]
    gp_Trsf                  = _g["gp_Trsf"]
    gp_Ax1                   = _g["gp_Ax1"]
    gp_Ax2                   = _g["gp_Ax2"]
    BRepAlgoAPI_Cut          = _g["BRepAlgoAPI_Cut"]
    BRepOffsetAPI_MakePipe   = _g["BRepOffsetAPI_MakePipe"]
    BRepBuilderAPI_Transform = _g["BRepBuilderAPI_Transform"]
    BRep_Builder             = _g["BRep_Builder"]
    TopoDS_Compound          = _g["TopoDS_Compound"]

    rc = p.cutting_diameter / 2.0
    rs = p.effective_shank_diameter / 2.0

    # back-face chamfer = 0.1 * shank dia, clamped so it can't eat the shank
    chamfer = min(0.1 * p.effective_shank_diameter,
                  rs * 0.8, max(p.shank_length * 0.5, 0.0))

    _p(_base_pct, "Building drill profile…")
    profile = _profile_points(p, rc, rs, chamfer)
    solid   = _revolve_profile(profile)

    # Budget: 5% of range for revolve, remainder split evenly across flutes
    revolve_budget = max(5, (_end_pct - _base_pct) // 10)
    flute_budget   = _end_pct - _base_pct - revolve_budget

    from app.utils.logging_setup import get_logger as _log
    _logger = _log()

    # ── flute geometry per the tool-design spec ───────────────────────────────
    # Cutter centre rides the Dc surface (helix_r = rc); cutter radius is chosen
    # so the deepest cut leaves the required CORE diameter between flutes.
    core_frac = _CORE_DIA_FRAC.get(p.n_flutes, _CORE_DIA_DEFAULT)
    flute_r   = rc * (1.0 - core_frac)        # depth = (1-core) of rc
    helix_r   = rc

    # Tip side: start the helix 10% of the point length PAST the apex (z<0) so
    # the flute runs fully out through the tip instead of stopping short.
    tip_over = _TIP_OVERSHOOT_FRAC * p.point_length
    if tip_over < EPS:
        tip_over = _TIP_OVERSHOOT_FRAC * p.cutting_diameter
    z_tip = -tip_over

    # Swap zone: last Dc×1.3 of body.
    swap_len     = p.cutting_diameter * _SWAP_LEN_FRAC
    z_swap_end   = p.x_body_end
    z_swap_start = max(z_swap_end - swap_len, p.x_point_base + EPS)

    ha_rad  = math.radians(p.helix_angle)
    cos_ha  = math.cos(ha_rad)
    sin_ha  = math.sin(ha_rad)
    pitch   = 2.0 * math.pi * helix_r / math.tan(ha_rad)
    w_rate  = 2.0 * math.pi / pitch   # rad per mm axial

    _logger.info("Flute spec: n=%d core=%.0f%% flute_r=%.3f  z_tip=%.3f  "
                 "swap_start=%.3f  body_end=%.3f",
                 p.n_flutes, core_frac * 100, flute_r, z_tip,
                 z_swap_start, z_swap_end)

    # ── Build flute-0 pipe once; rotate copies for flutes 1..n-1 ────────────
    # One MakePipe call; all n flutes come from BRepBuilderAPI_Transform (fast).
    # Spine = helix + smooth run-out (swap) so the groove lifts cleanly off the
    # body by z_swap_end.

    build_pct = _base_pct + revolve_budget
    _p(build_pct, "Building flute helix + swap…")
    main_wire_0 = _make_flute_spine(z_tip, z_swap_start, z_swap_end, helix_r,
                                    p.helix_angle, 0.0, flute_r)

    # Disk profile at z_tip, phase=0 tangent = (0, cos_ha, sin_ha)
    prof_0 = _disk_profile(gp_Pnt(helix_r, 0.0, z_tip),
                           gp_Dir(0.0, cos_ha, sin_ha), flute_r)

    _p(build_pct + flute_budget // 3, "Sweeping flute pipe…")
    try:
        main_pipe = BRepOffsetAPI_MakePipe(main_wire_0, prof_0)
        main_pipe.Build()
    except Exception as exc:
        raise RuntimeError(f"Flute MakePipe failed: {exc}") from exc
    if not main_pipe.IsDone():
        raise RuntimeError("Flute MakePipe failed (IsDone=False)")
    main_shape_0 = main_pipe.Shape()
    time.sleep(0)

    # ── Compound: all rotated copies → ONE Boolean cut ───────────────────────
    # Cutting the original (simple) cylinder once with a compound tool is faster
    # than N sequential cuts on an ever-growing complex solid.
    _p(build_pct + flute_budget * 2 // 3, "Building flute compound…")
    ax_z     = gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    compound = TopoDS_Compound()
    bb       = BRep_Builder()
    bb.MakeCompound(compound)
    bb.Add(compound, main_shape_0)
    for i in range(1, p.n_flutes):
        trsf = gp_Trsf()
        trsf.SetRotation(ax_z, 2.0 * math.pi * i / p.n_flutes)
        bb.Add(compound, BRepBuilderAPI_Transform(main_shape_0, trsf, True).Shape())
    time.sleep(0)

    _p(build_pct + flute_budget * 2 // 3 + 2, "Cutting all flutes…")
    cut = BRepAlgoAPI_Cut(solid, compound)
    cut.SetRunParallel(True)
    # No SetFuzzyValue: fuzzy Booleans are 1.5–2× slower here and the cut is
    # clean without it.  The run-out is tangent to the body, which fuzzy makes
    # WORSE, not better.
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("Compound flute cut failed (IsDone=False)")
    solid = cut.Shape()
    time.sleep(0)

    _p(_end_pct, "Flutes done")
    _logger.info("All %d flutes cut.", p.n_flutes)
    return solid


# ══════════════════════════════════════════════════════════════ mesh export ══

def _tessellate(shape) -> dict:
    BRepMesh_IncrementalMesh(shape, _MESH_DEFLECTION).Perform()
    time.sleep(0)   # release GIL after mesh generation

    verts, idxs, offset = [], [], 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    face_count = 0
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        loc  = TopLoc_Location()
        tri  = BRep_Tool.Triangulation_s(face, loc)

        if tri is not None and tri.NbTriangles() > 0:
            n       = tri.NbNodes()
            flipped = (face.Orientation() == TopAbs_REVERSED)
            # Pre-build per-face lists then extend once — avoids repeated
            # list-grow overhead and reduces GIL hold time per iteration.
            fv = [0.0] * (n * 3)
            for k in range(n):
                pnt = tri.Node(k + 1)
                fv[k*3], fv[k*3+1], fv[k*3+2] = pnt.X(), pnt.Y(), pnt.Z()
            verts.extend(fv)

            nt = tri.NbTriangles()
            fi = [0] * (nt * 3)
            for k in range(nt):
                a, b, c = tri.Triangle(k + 1).Get()
                a -= 1; b -= 1; c -= 1
                base = k * 3
                if flipped:
                    fi[base], fi[base+1], fi[base+2] = offset+a, offset+c, offset+b
                else:
                    fi[base], fi[base+1], fi[base+2] = offset+a, offset+b, offset+c
            idxs.extend(fi)
            offset += n

        face_count += 1
        if face_count % 20 == 0:
            time.sleep(0)   # periodic GIL release so main thread stays live
        exp.Next()

    return {'v': verts, 'i': idxs}


# ══════════════════════════════════════════════════════════ Node.js projection ══

def _resolve_node() -> str:
    """Return the absolute path to node.exe (prevents PATH-hijack on Windows)."""
    import shutil
    node = shutil.which("node")
    if not node:
        raise RuntimeError(
            "Node.js not found on PATH. Install Node.js to generate proposal DXFs.")
    return os.path.abspath(node)


def _project_via_nodejs(mesh_data: dict) -> dict:
    script = os.path.normpath(_node_script())
    node   = _resolve_node()

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                     delete=False, encoding='utf-8') as mf:
        json.dump(mesh_data, mf)
        mesh_path = mf.name
    seg_path = mesh_path.replace('.json', '_segs.json')

    try:
        result = subprocess.run(
            [node, script, mesh_path, seg_path],
            capture_output=True, text=True, timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"gen_proposal.mjs failed (exit {result.returncode}):\n"
                f"{result.stderr.strip()}")
        with open(seg_path, 'r', encoding='utf-8') as sf:
            raw = json.load(sf)
    finally:
        for f in (mesh_path, seg_path):
            try:
                os.remove(f)
            except OSError:
                pass

    # side raw [x,z,x,z] -> DXF (axial=X, radial=Y)
    side = [(z1, x1, z2, x2) for (x1, z1, x2, z2) in raw['side']]
    side = _clean_side_segments(side)
    return {'side': side}


def _clean_side_segments(segs, q: float = 0.02, min_len: float = 0.05):
    """De-noise the projected side view.

    The visible-edge projection of the flute run-out emits a lot of junk:
    zero-length degenerate segments and exact/near-duplicate edges where mesh
    facets overlap.  In the run-out zone this junk outnumbers the real curves
    ~2:1 and makes the swap look like a scribble.  We drop sub-min_len segments
    and deduplicate edges snapped to a q-mm grid (undirected).  Real flute and
    silhouette curves — which are long, non-duplicated chains — survive intact.
    """
    seen = set()
    out  = []
    for (z1, x1, z2, x2) in segs:
        if math.hypot(z2 - z1, x2 - x1) < min_len:
            continue                              # degenerate / sub-pixel stub
        a = (round(z1 / q), round(x1 / q))
        b = (round(z2 / q), round(x2 / q))
        if a == b:
            continue                              # collapses to a point on grid
        key = (a, b) if a <= b else (b, a)        # undirected
        if key in seen:
            continue                              # duplicate overlapping edge
        seen.add(key)
        out.append((z1, x1, z2, x2))
    return out


# ══════════════════════════════════════════════════════════ DXF annotations ══

def _ensure_layers(doc):
    # Colours: drill model = yellow(2), centreline = cyan(4), dims = red(1)
    specs = [
        ("OUTLINE", 2),   ("FRONT", 2),   ("CENTER", 4),   ("DIM", 1),
    ]
    for name, color in specs:
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    if "CENTER" not in doc.linetypes:
        doc.linetypes.add("CENTER", pattern=[2.0, 1.25, -0.25, 0.25, -0.25])


def _dim_override(h):
    # dimlfac MUST be 1 — ezdxf's EZDXF dimstyle ships with dimlfac=100 (1:100
    # plan scale), which would render 100 mm as 10000.  Colours: dim lines +
    # extension lines red(1), dim text yellow(2).
    return {
        "dimtxt": h, "dimasz": h, "dimexe": h * 0.5, "dimexo": h * 0.6,
        "dimgap": h * 0.3, "dimdec": 2, "dimtad": 1,
        "dimlfac": 1.0, "dimclrd": 1, "dimclre": 1, "dimclrt": 2,
    }


def _add_centerlines(msp, p, rc, front_cx, front_r, pad):
    msp.add_line((-pad, 0), (p.overall_length + pad, 0),
                 dxfattribs={"layer": "CENTER", "linetype": "CENTER"})
    ext = front_r + pad
    msp.add_line((front_cx - ext, 0), (front_cx + ext, 0),
                 dxfattribs={"layer": "CENTER", "linetype": "CENTER"})
    msp.add_line((front_cx, -ext), (front_cx, ext),
                 dxfattribs={"layer": "CENTER", "linetype": "CENTER"})


def _add_dims(msp, p, rc, rs, h):
    ov   = _dim_override(h)
    rmax = max(rc, rs)
    dia  = "⌀"   # ⌀

    # Dc (vertical) — left of the tip
    dc = msp.add_linear_dim(
        base=(-(rc + h * 6), 0), p1=(p.x_point_base, rc), p2=(p.x_point_base, -rc),
        angle=90, dimstyle="EZDXF",
        override={**ov, "dimpost": dia + "<>"}, dxfattribs={"layer": "DIM"})
    dc.render()

    # D (vertical) — right of the back face, only if it differs
    if abs(rs - rc) > 1e-3:
        dd = msp.add_linear_dim(
            base=(p.x_end + rs + h * 6, 0), p1=(p.x_end, rs), p2=(p.x_end, -rs),
            angle=90, dimstyle="EZDXF",
            override={**ov, "dimpost": dia + "<>"}, dxfattribs={"layer": "DIM"})
        dd.render()

    # OAL (horizontal) — far below
    oal = msp.add_linear_dim(
        base=(0, -(rmax + h * 9)), p1=(0, 0), p2=(p.overall_length, 0),
        angle=0, dimstyle="EZDXF", override=ov, dxfattribs={"layer": "DIM"})
    oal.render()

    # Ls (shank length, horizontal) — nearer below
    ls = msp.add_linear_dim(
        base=(0, -(rmax + h * 5)), p1=(p.x_shank_start, -rs), p2=(p.x_end, -rs),
        angle=0, dimstyle="EZDXF", override=ov, dxfattribs={"layer": "DIM"})
    ls.render()

    # Point angle (angular) — at the tip.  The arc-location point decides which
    # sector is measured; it must sit INSIDE the cone opening (on the +X axis
    # between the two rays) so we get the included angle (e.g. 140) and not its
    # reflex (220).
    if p.point_length > EPS:
        try:
            # p1/p2 ordered so the CCW sweep p1->p2 is the INCLUDED angle
            # (e.g. 140), not the reflex (220).
            from app.utils.logging_setup import get_logger as _log2
            ang = msp.add_angular_dim_3p(
                base=(rc * 1.5, 0.0),
                center=(0, 0), p1=(p.x_point_base, -rc), p2=(p.x_point_base, rc),
                dimstyle="EZDXF",
                override={**ov, "dimaunit": 0, "dimadec": 1},
                dxfattribs={"layer": "DIM"})
            ang.render()
        except Exception as exc:
            _log2().warning("Angular point-angle dimension failed (non-fatal): %s", exc)


def _add_end_view(msp, p, rc, front_cx):
    """Analytical drill tip end-view (looking at the cutting face along the axis).

    Draws: outer body circle, two cutting lips, chisel edge.
    Uses only basic DXF entities so the result is clean and exact.
    """
    cx, cy  = front_cx, 0.0
    web_r   = rc * 0.15          # web / core radius (≈ 15 % of Dc/2)

    # Perfect outer body circle
    msp.add_circle((cx, cy), rc, dxfattribs={"layer": "FRONT"})

    # Lip direction: right-hand drill, first cutting lip exits the outer circle at
    # (90° - helix_angle) from the +X axis in the end view.
    lip_ang = math.radians(90.0 - p.helix_angle)
    chi_ang = lip_ang + math.pi / 2       # chisel edge ⊥ to lips

    cos_l, sin_l = math.cos(lip_ang), math.sin(lip_ang)
    cos_c, sin_c = math.cos(chi_ang), math.sin(chi_ang)

    if p.n_flutes == 2:
        # Two cutting lips (parallel to each other, offset by the chisel edge)
        # and one short chisel-edge line connecting their inner ends.
        p1 = (cx + rc    *  cos_l, cy + rc    *  sin_l)   # lip 1 outer
        p2 = (cx - rc    *  cos_l, cy - rc    *  sin_l)   # lip 2 outer
        b1 = (cx + web_r *  cos_c, cy + web_r *  sin_c)   # lip 1 inner
        b2 = (cx - web_r *  cos_c, cy - web_r *  sin_c)   # lip 2 inner

        msp.add_line(b1, p1, dxfattribs={"layer": "FRONT"})   # cutting lip 1
        msp.add_line(b2, p2, dxfattribs={"layer": "FRONT"})   # cutting lip 2
        msp.add_line(b1, b2, dxfattribs={"layer": "FRONT"})   # chisel edge
    else:
        # For 3 / 4 flutes: n equally-spaced lips radiating from a web circle
        for i in range(p.n_flutes):
            a    = lip_ang + 2 * math.pi * i / p.n_flutes
            outer = (cx + rc    * math.cos(a),          cy + rc    * math.sin(a))
            inner = (cx + web_r * math.cos(a + math.pi), cy + web_r * math.sin(a + math.pi))
            msp.add_line(inner, outer, dxfattribs={"layer": "FRONT"})
        msp.add_circle((cx, cy), web_r, dxfattribs={"layer": "FRONT"})


# ══════════════════════════════════════════════════════════ public entry point ══

def _build_geometry_dxf(p: DrillProposalParams, geom_path: str, *,
                         _progress=None, _mesh_cb=None) -> dict:
    """Write the geometry-only DXF (views + centrelines + dims). Returns anchor
    points the AutoCAD stage needs to place the GD&T / datum blocks.

    The side view is PROJECTED from the 3D solid (silhouette + flute/helix
    edges) via the node.js three-edge-projection — this is the whole point of
    building the solid, and is what puts the helical flutes in the drawing.

    _progress(percent, message) is called at each pipeline stage.
    _mesh_cb(verts_flat, indices_flat) fires once after tessellation so the 3D
    viewer can show the solid while accoreconsole finalizes the DXF.
    """
    def _p(pct, msg):
        if _progress:
            _progress(pct, msg)

    rc = p.cutting_diameter / 2.0
    rs = p.effective_shank_diameter / 2.0

    solid = _build_solid_cached(p, _progress=_progress, _base_pct=5, _end_pct=58)

    _p(60, "Tessellating mesh…")
    mesh_data = _tessellate(solid)

    if _mesh_cb:
        _mesh_cb(mesh_data['v'], mesh_data['i'])

    _p(65, "Projecting edges…")
    views = _project_via_nodejs(mesh_data)

    _p(78, "Writing DXF…")
    feature = max(p.overall_length, p.cutting_diameter * 4.0,
                  p.effective_shank_diameter * 4.0, 1.0)
    h   = max(feature * 0.018, 0.8)
    pad = rc * 0.5

    front_r  = max(rc, rs)
    front_cx = p.overall_length + front_r * 2.5

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.header["$LTSCALE"]  = max(feature * 0.02, 0.5)
    msp = doc.modelspace()
    _ensure_layers(doc)

    for z1, x1, z2, x2 in views['side']:
        msp.add_line((z1, x1), (z2, x2), dxfattribs={"layer": "OUTLINE"})

    _add_end_view(msp, p, rc, front_cx)
    _add_centerlines(msp, p, rc, front_cx, front_r, pad)
    _add_dims(msp, p, rc, rs, h)

    doc.saveas(geom_path)

    # GD&T frame above the cutting end; datum on the shank toward the back
    return {
        "h":        h,
        "gdt_ins":  (p.x_point_base, rc + h * 6),
        "dat_ins":  (p.x_shank_start + p.shank_length * 0.6, rs),
    }


def preview_solid(params: DrillProposalParams, *,
                  progress=None, mesh_cb=None) -> None:
    """Build the drill solid and tessellate it — no DXF, no node.js, no AutoCAD.

    Uses aggressively coarser helix/mesh settings than the DXF path so the
    solid builds in roughly half the time.  Visual quality is sufficient for
    interactive preview; the DXF path restores full quality.
    """
    def _p(pct, msg):
        if progress:
            progress(pct, msg)

    errs = params.validate()
    if errs:
        raise ValueError("\n".join(errs))

    import sys as _sys
    _mod = _sys.modules[__name__]

    # Stash DXF-quality settings and switch to preview-quality ones.
    # Safe: buttons are disabled while any worker runs so no concurrent access.
    _orig_helix_pts  = _mod._HELIX_PTS_PER_TURN
    _orig_helix_min  = _mod._HELIX_MIN_PTS
    _orig_deflection = _mod._MESH_DEFLECTION
    _mod._HELIX_PTS_PER_TURN = 14   # smooth helix, fast pipe+Boolean (cliff is 24)
    _mod._HELIX_MIN_PTS      = 8
    _mod._MESH_DEFLECTION    = 0.18 # coarser mesh; fine for interactive preview

    try:
        _p(2, "Starting…")
        solid = _build_solid(params, _progress=progress, _base_pct=5, _end_pct=88)

        _p(90, "Tessellating mesh…")
        mesh_data = _tessellate(solid)

        if mesh_cb:
            mesh_cb(mesh_data['v'], mesh_data['i'])

        _p(100, "Done")

    finally:
        # Always restore — even if _build_solid raises
        _mod._HELIX_PTS_PER_TURN = _orig_helix_pts
        _mod._HELIX_MIN_PTS      = _orig_helix_min
        _mod._MESH_DEFLECTION    = _orig_deflection


def generate_step(params: DrillProposalParams, step_path: str, *,
                  progress=None) -> None:
    """Build the drill solid and export it as a STEP (AP203) file.

    This is independent of the DXF pipeline — call it to get a 3D model file
    that any CAD system can import.
    """
    def _p(pct, msg):
        if progress:
            progress(pct, msg)

    errs = params.validate()
    if errs:
        raise ValueError("\n".join(errs))

    _p(2, "Starting STEP export…")
    solid = _build_solid_cached(params, _progress=progress, _base_pct=5, _end_pct=88)

    _p(90, "Writing STEP file…")
    import sys as _sys
    _g = _sys.modules[__name__].__dict__
    if "STEPControl_Writer" not in _g:
        from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
        from OCP.Interface import Interface_Static
        _g.update({k: v for k, v in locals().items() if not k.startswith("_")})

    STEPControl_Writer  = _g["STEPControl_Writer"]
    STEPControl_AsIs    = _g["STEPControl_AsIs"]
    Interface_Static    = _g["Interface_Static"]

    writer = STEPControl_Writer()
    Interface_Static.SetCVal_s("write.step.schema", "AP203")
    writer.Transfer(solid, STEPControl_AsIs)
    status = writer.Write(step_path)
    if status != 1:
        raise RuntimeError(
            f"STEP write failed (OCC status={status}). "
            f"Check that the output directory exists and is writable.")

    _p(100, "STEP export complete")


def generate(params: DrillProposalParams, out_path: str, *,
             progress=None, mesh_cb=None) -> None:
    """Generate the proposal DXF for the given parameters.

    Args:
        params:   Drill parameters (already validated by caller, re-validated here).
        out_path: Destination .dxf file path.
        progress: Optional callable(percent: int, message: str) called at each stage.
        mesh_cb:  Optional callable(verts: list, indices: list) called once after
                  tessellation so the UI can display the 3D model while AutoCAD
                  is still finalising the DXF in the background stage.
    """
    def _p(pct, msg):
        if progress:
            progress(pct, msg)

    errs = params.validate()
    if errs:
        raise ValueError("\n".join(errs))

    _p(2, "Starting…")

    # Stage 1 — geometry DXF (pure ezdxf)
    fd, geom_path = tempfile.mkstemp(suffix="_geom.dxf")
    os.close(fd)
    try:
        anchors = _build_geometry_dxf(params, geom_path,
                                       _progress=progress, _mesh_cb=mesh_cb)
        # Stage 2 — AutoCAD inserts the real DWG blocks/template and native-saves
        # the result so it opens read-WRITE (see memory dxf-open-readwrite).
        _p(82, "Finalizing in AutoCAD…")
        from app.dxf.proposal_acad import finalize_with_acad
        finalize_with_acad(geom_path, params, anchors, out_path)
        _p(100, "Done")
    finally:
        try:
            os.remove(geom_path)
        except OSError:
            pass
