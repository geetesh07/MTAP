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

import ezdxf

from OCP.gp import gp_Pnt, gp_Dir, gp_Ax1, gp_Ax2
from OCP.BRepPrimAPI import (BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone,
                             BRepPrimAPI_MakeRevol)
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge,
                                BRepBuilderAPI_MakeWire,
                                BRepBuilderAPI_MakeFace)
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.TopLoc import TopLoc_Location
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.TColgp import TColgp_HArray1OfPnt
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopoDS import TopoDS

from app.engine.tools.drill import DrillProposalParams
from app.utils.config import resource_path

EPS = 1e-6
_HELIX_PTS_PER_TURN = 120
_FLUTE_RADIUS_FRAC  = 0.38
_MESH_DEFLECTION    = 0.05   # mm — smaller = finer mesh

def _node_script() -> str:
    return resource_path(os.path.join("nodejs", "gen_proposal.mjs"))


# ══════════════════════════════════════════════════════════ solid construction ══

def _make_helix_wire(z_start, length, radius, helix_angle_deg, phase_rad):
    pitch = math.pi * 2 * radius / math.tan(math.radians(helix_angle_deg))
    turns = length / pitch
    n_pts = max(12, int(_HELIX_PTS_PER_TURN * turns))

    arr = TColgp_HArray1OfPnt(1, n_pts + 1)
    for i in range(n_pts + 1):
        t     = i / n_pts
        z     = z_start + length * t
        angle = 2 * math.pi * turns * t + phase_rad
        arr.SetValue(i + 1, gp_Pnt(radius * math.cos(angle),
                                   radius * math.sin(angle), z))
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


def _build_solid(p: DrillProposalParams):
    rc = p.cutting_diameter / 2.0
    rs = p.effective_shank_diameter / 2.0

    # back-face chamfer = 0.1 * shank dia, clamped so it can't eat the shank
    chamfer = min(0.1 * p.effective_shank_diameter,
                  rs * 0.8, max(p.shank_length * 0.5, 0.0))

    profile = _profile_points(p, rc, rs, chamfer)
    solid   = _revolve_profile(profile)

    # Helical flute cuts — swept only along the Dc body region
    flute_r = rc * _FLUTE_RADIUS_FRAC
    helix_r = rc
    for i in range(p.n_flutes):
        phase = (2 * math.pi * i) / p.n_flutes
        spine = _make_helix_wire(p.point_length, p.body_length, helix_r,
                                 p.helix_angle, phase)

        ha  = math.radians(p.helix_angle)
        tx  = -math.sin(phase) * math.cos(ha)
        ty  =  math.cos(phase) * math.cos(ha)
        tz  =  math.sin(ha)
        mag = math.sqrt(tx*tx + ty*ty + tz*tz)
        tang = gp_Dir(tx/mag, ty/mag, tz/mag)

        start_pt = gp_Pnt(helix_r * math.cos(phase),
                          helix_r * math.sin(phase),
                          p.point_length)
        prof = _disk_profile(start_pt, tang, flute_r)

        try:
            pipe = BRepOffsetAPI_MakePipe(spine, prof)
            pipe.Build()
            if pipe.IsDone():
                cut = BRepAlgoAPI_Cut(solid, pipe.Shape())
                cut.Build()
                if cut.IsDone():
                    solid = cut.Shape()
        except Exception:
            pass

    return solid


# ══════════════════════════════════════════════════════════════ mesh export ══

def _tessellate(shape) -> dict:
    BRepMesh_IncrementalMesh(shape, _MESH_DEFLECTION).Perform()

    verts, idxs, offset = [], [], 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        loc  = TopLoc_Location()
        tri  = BRep_Tool.Triangulation_s(face, loc)

        if tri is not None and tri.NbTriangles() > 0:
            n       = tri.NbNodes()
            flipped = (face.Orientation() == TopAbs_REVERSED)
            for k in range(1, n + 1):
                pnt = tri.Node(k)
                verts += [pnt.X(), pnt.Y(), pnt.Z()]
            for k in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(k).Get()
                a -= 1; b -= 1; c -= 1
                idxs += ([offset+a, offset+c, offset+b] if flipped
                         else [offset+a, offset+b, offset+c])
            offset += n
        exp.Next()

    return {'v': verts, 'i': idxs}


# ══════════════════════════════════════════════════════════ Node.js projection ══

def _project_via_nodejs(mesh_data: dict) -> dict:
    script = os.path.normpath(_node_script())

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                     delete=False, encoding='utf-8') as mf:
        json.dump(mesh_data, mf)
        mesh_path = mf.name
    seg_path = mesh_path.replace('.json', '_segs.json')

    try:
        result = subprocess.run(
            ['node', script, mesh_path, seg_path],
            capture_output=True, text=True, timeout=180,
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

    # side raw [x,z,x,z] -> DXF (axial=X, radial=Y) ; front already (X,Y)
    side  = [(z1, x1, z2, x2) for (x1, z1, x2, z2) in raw['side']]
    front = raw['front']
    return {'side': side, 'front': front}


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
            ang = msp.add_angular_dim_3p(
                base=(rc * 1.5, 0.0),
                center=(0, 0), p1=(p.x_point_base, -rc), p2=(p.x_point_base, rc),
                dimstyle="EZDXF",
                override={**ov, "dimaunit": 0, "dimadec": 1},
                dxfattribs={"layer": "DIM"})
            ang.render()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════ public entry point ══

def _build_geometry_dxf(p: DrillProposalParams, geom_path: str) -> dict:
    """Write the geometry-only DXF (views + centrelines + dims). Returns anchor
    points the AutoCAD stage needs to place the GD&T / datum blocks."""
    rc = p.cutting_diameter / 2.0
    rs = p.effective_shank_diameter / 2.0

    solid     = _build_solid(p)
    mesh_data = _tessellate(solid)
    views     = _project_via_nodejs(mesh_data)

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
    for x1, y1, x2, y2 in views['front']:
        msp.add_line((x1 + front_cx, y1), (x2 + front_cx, y2),
                     dxfattribs={"layer": "FRONT"})

    _add_centerlines(msp, p, rc, front_cx, front_r, pad)
    _add_dims(msp, p, rc, rs, h)

    doc.saveas(geom_path)

    # GD&T frame above the cutting end; datum on the shank toward the back
    return {
        "h":        h,
        "gdt_ins":  (p.x_point_base, rc + h * 6),
        "dat_ins":  (p.x_shank_start + p.shank_length * 0.6, rs),
    }


def generate(params: DrillProposalParams, out_path: str) -> None:
    errs = params.validate()
    if errs:
        raise ValueError("\n".join(errs))

    # Stage 1 — geometry DXF (pure ezdxf)
    fd, geom_path = tempfile.mkstemp(suffix="_geom.dxf")
    os.close(fd)
    try:
        anchors = _build_geometry_dxf(params, geom_path)
        # Stage 2 — AutoCAD inserts the real DWG blocks/template and native-saves
        # the result so it opens read-WRITE (see memory dxf-open-readwrite).
        from app.dxf.proposal_acad import finalize_with_acad
        finalize_with_acad(geom_path, params, anchors, out_path)
    finally:
        try:
            os.remove(geom_path)
        except OSError:
            pass
