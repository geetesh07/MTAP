"""
proposal_dxf.py — Proposal Drawing DXF generator.

Pipeline:
  1. Build drill solid via OCC boolean cuts (same as always)
  2. Tessellate the solid to a triangle mesh JSON
  3. Call nodejs/gen_proposal.mjs  →  three-edge-projection (ProjectionGenerator)
     projects along the Y axis and returns visible edge segments
  4. Write those segments to DXF with ezdxf
"""

import json
import math
import os
import subprocess
import tempfile
import ezdxf

from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut
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

_HELIX_PTS_PER_TURN = 120
_FLUTE_RADIUS_FRAC  = 0.38
_MESH_DEFLECTION    = 0.05   # mm; smaller = finer mesh, slower tessellation

def _node_script() -> str:
    """Return absolute path to gen_proposal.mjs — works in dev and frozen exe."""
    return resource_path(os.path.join("nodejs", "gen_proposal.mjs"))


# ──────────────────────────────────────────────────────── solid construction ──

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


def _build_solid(Dc, shank_dia, point_length, body_length, shank_length,
                 helix_angle, n_flutes):
    rc = Dc / 2.0

    body_ax = gp_Ax2(gp_Pnt(0, 0, point_length), gp_Dir(0, 0, 1))
    solid   = BRepPrimAPI_MakeCylinder(body_ax, rc,
                                       body_length + shank_length).Shape()

    if point_length > 1e-6:
        tip_ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
        tip    = BRepPrimAPI_MakeCone(tip_ax, 0.0, rc, point_length).Shape()
        fuse   = BRepAlgoAPI_Fuse(solid, tip)
        fuse.Build()
        solid  = fuse.Shape()

    flute_r = rc * _FLUTE_RADIUS_FRAC
    helix_r = rc
    for i in range(n_flutes):
        phase   = (2 * math.pi * i) / n_flutes
        spine   = _make_helix_wire(point_length, body_length, helix_r,
                                   helix_angle, phase)

        ha  = math.radians(helix_angle)
        tx  = -math.sin(phase) * math.cos(ha)
        ty  =  math.cos(phase) * math.cos(ha)
        tz  =  math.sin(ha)
        mag = math.sqrt(tx*tx + ty*ty + tz*tz)
        tang = gp_Dir(tx/mag, ty/mag, tz/mag)

        start_pt = gp_Pnt(helix_r * math.cos(phase),
                          helix_r * math.sin(phase),
                          point_length)
        profile = _disk_profile(start_pt, tang, flute_r)

        try:
            pipe = BRepOffsetAPI_MakePipe(spine, profile)
            pipe.Build()
            if pipe.IsDone():
                cut = BRepAlgoAPI_Cut(solid, pipe.Shape())
                cut.Build()
                if cut.IsDone():
                    solid = cut.Shape()
        except Exception:
            pass

    return solid


# ──────────────────────────────────────────────────────── mesh export ──

def _tessellate(shape):
    """Return {'v': [x,y,z,...], 'i': [a,b,c,...]} flat arrays."""
    BRepMesh_IncrementalMesh(shape, _MESH_DEFLECTION).Perform()

    verts = []
    idxs  = []
    offset = 0

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        loc  = TopLoc_Location()
        tri  = BRep_Tool.Triangulation_s(face, loc)

        if tri is not None and tri.NbTriangles() > 0:
            n       = tri.NbNodes()
            flipped = (face.Orientation() == TopAbs_REVERSED)

            for k in range(1, n + 1):
                p = tri.Node(k)
                verts += [p.X(), p.Y(), p.Z()]

            for k in range(1, tri.NbTriangles() + 1):
                t = tri.Triangle(k)
                a, b, c = t.Get()
                a -= 1; b -= 1; c -= 1
                if flipped:
                    idxs += [offset+a, offset+c, offset+b]
                else:
                    idxs += [offset+a, offset+b, offset+c]

            offset += n

        exp.Next()

    return {'v': verts, 'i': idxs}


# ──────────────────────────────────────────────────────── Node.js projection ──

def _project_via_nodejs(mesh_data: dict) -> list:
    """
    Call gen_proposal.mjs; returns list of (x1,y1,x2,y2) DXF line segments.
    """
    script = os.path.normpath(_node_script())

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                     delete=False, encoding='utf-8') as mf:
        json.dump(mesh_data, mf)
        mesh_path = mf.name

    seg_path = mesh_path.replace('.json', '_segs.json')

    try:
        result = subprocess.run(
            ['node', script, mesh_path, seg_path],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"gen_proposal.mjs failed (exit {result.returncode}):\n"
                f"{result.stderr.strip()}"
            )

        with open(seg_path, 'r', encoding='utf-8') as sf:
            segs = json.load(sf)   # [[z1,x1,z2,x2], ...]
    finally:
        for p in (mesh_path, seg_path):
            try:
                os.remove(p)
            except OSError:
                pass

    return segs


# ──────────────────────────────────────────────────────── public entry point ──

def generate(params: DrillProposalParams, out_path: str) -> None:
    errs = params.validate()
    if errs:
        raise ValueError("\n".join(errs))

    p  = params
    rc = p.cutting_diameter / 2.0

    solid = _build_solid(
        Dc           = p.cutting_diameter,
        shank_dia    = p.effective_shank_diameter,
        point_length = p.point_length,
        body_length  = p.body_length,
        shank_length = p.shank_length,
        helix_angle  = p.helix_angle,
        n_flutes     = p.n_flutes,
    )

    mesh_data = _tessellate(solid)
    segs      = _project_via_nodejs(mesh_data)   # [[z1,x1,z2,x2], ...]

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    all_x, all_y = [], []
    for z1, x1, z2, x2 in segs:
        msp.add_line((z1, x1), (z2, x2), dxfattribs={"layer": "0"})
        all_x += [z1, z2]
        all_y += [x1, x2]

    if all_x:
        pad = rc * 0.15
        doc.header["$INSUNITS"] = 4
        doc.header["$EXTMIN"] = (min(all_x) - pad, min(all_y) - pad, 0.0)
        doc.header["$EXTMAX"] = (max(all_x) + pad, max(all_y) + pad, 0.0)

    doc.saveas(out_path)
