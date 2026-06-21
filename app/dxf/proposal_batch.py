"""
proposal_batch.py — generate a matrix of proposal DXFs (one parameter varied at
a time from a baseline).  Shared by the CLI (MTAP.exe --gen-proposals <dir>) and
the dev script scripts/batch_proposals.py.
"""

import os
import time

from app.engine.tools.drill import DrillProposalParams
from app.dxf.proposal_dxf import generate

# Baseline — each set overrides only the parameter under study.
BASE = dict(cutting_diameter=10, shank_diameter=10, overall_length=100,
            shank_length=40, point_angle=140, helix_angle=30, n_flutes=2,
            reinforcement=False, reinforcement_angle=30, runout=0.010)

# (category, name, overrides)
MATRIX = [
    ("01_Flutes", "Flutes_2", dict(n_flutes=2)),
    ("01_Flutes", "Flutes_3", dict(n_flutes=3)),
    ("01_Flutes", "Flutes_4", dict(n_flutes=4)),

    ("02_Helix", "Helix_15deg", dict(helix_angle=15)),
    ("02_Helix", "Helix_25deg", dict(helix_angle=25)),
    ("02_Helix", "Helix_35deg", dict(helix_angle=35)),
    ("02_Helix", "Helix_45deg", dict(helix_angle=45)),

    ("03_PointAngle", "Point_90deg",  dict(point_angle=90)),
    ("03_PointAngle", "Point_118deg", dict(point_angle=118)),
    ("03_PointAngle", "Point_140deg", dict(point_angle=140)),
    ("03_PointAngle", "Point_150deg", dict(point_angle=150)),

    ("04_Diameters", "Dc6_D6",         dict(cutting_diameter=6,  shank_diameter=6,  shank_length=25)),
    ("04_Diameters", "Dc10_D10",       dict(cutting_diameter=10, shank_diameter=10)),
    ("04_Diameters", "Dc16_D16",       dict(cutting_diameter=16, shank_diameter=16, shank_length=50)),
    ("04_Diameters", "Dc12_D16_reinf", dict(cutting_diameter=12, shank_diameter=16,
                                            shank_length=45, reinforcement=True)),
    ("04_Diameters", "Dc8_D10_reinf",  dict(cutting_diameter=8,  shank_diameter=10,
                                            shank_length=35, reinforcement=True)),

    ("05_Lengths", "OAL60_Ls30",  dict(overall_length=60,  shank_length=30)),
    ("05_Lengths", "OAL100_Ls40", dict(overall_length=100, shank_length=40)),
    ("05_Lengths", "OAL150_Ls50", dict(overall_length=150, shank_length=50)),
    ("05_Lengths", "OAL200_Ls60", dict(overall_length=200, shank_length=60)),
]


def _find_case(name: str):
    for cat, nm, ov in MATRIX:
        if nm == name:
            return cat, nm, ov
    return None


def generate_one(name: str, out_path: str) -> None:
    """Generate a single matrix case by name (used by the per-file subprocess)."""
    case = _find_case(name)
    if not case:
        raise ValueError(f"unknown case: {name}")
    _, _, ov = case
    p = DrillProposalParams(**{**BASE, **ov})
    p.derive()
    errs = p.validate()
    if errs:
        raise ValueError("; ".join(errs))
    generate(p, out_path)


def generate_matrix(out_root: str, log=print) -> int:
    """Generate the full matrix IN-PROCESS. Returns count generated.

    Note: the frozen exe can accumulate native (OpenCASCADE) state across many
    builds in one process and crash — prefer generate_matrix_isolated() there."""
    os.makedirs(out_root, exist_ok=True)
    t0 = time.time()
    made = 0
    for category, name, ov in MATRIX:
        p = DrillProposalParams(**{**BASE, **ov})
        p.derive()
        errs = p.validate()
        folder = os.path.join(out_root, category)
        os.makedirs(folder, exist_ok=True)
        out = os.path.join(folder, f"{name}.dxf")
        if errs:
            log(f"  SKIP {category}/{name}: {errs}")
            continue
        t = time.time()
        generate(p, out)
        made += 1
        log(f"  {category}/{name:<22} {time.time()-t:5.1f}s")
    log(f"DONE  {made} DXFs  total {time.time()-t0:.0f}s  ->  {out_root}")
    return made


def generate_matrix_isolated(out_root: str, child_prefix: list, log=print) -> int:
    """Generate each DXF in a FRESH child process (child_prefix + --gen-one name
    out) so OpenCASCADE native state never accumulates.  Robust for the exe."""
    import subprocess
    os.makedirs(out_root, exist_ok=True)
    t0 = time.time()
    made = 0
    for category, name, ov in MATRIX:
        folder = os.path.join(out_root, category)
        os.makedirs(folder, exist_ok=True)
        out = os.path.join(folder, f"{name}.dxf")
        t = time.time()
        # The frozen OpenCASCADE build crashes intermittently even on a single
        # build, so retry a few times — a fresh process almost always succeeds.
        ok = False
        for attempt in range(1, 4):
            try:
                r = subprocess.run(child_prefix + ["--gen-one", name, out],
                                   timeout=300,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
            except subprocess.TimeoutExpired:
                log(f"  TIMEOUT {category}/{name} (attempt {attempt})")
                continue
            if r.returncode == 0 and os.path.exists(out):
                ok = True
                break
            log(f"  retry {category}/{name} (attempt {attempt}, rc={r.returncode})")
        if ok:
            made += 1
            log(f"  OK   {category}/{name:<22} {time.time()-t:5.1f}s")
        else:
            log(f"  FAIL {category}/{name}")
    log(f"DONE  {made}/{len(MATRIX)} DXFs  total {time.time()-t0:.0f}s  ->  {out_root}")
    return made
