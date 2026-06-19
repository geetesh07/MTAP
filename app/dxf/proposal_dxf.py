"""
proposal_dxf.py — calls gen_proposal.cjs (Node.js + opencascade.js) to produce
a real HLR-projected DXF, identical pipeline to the 3D project.

Requires Node.js on PATH. All JS dependencies (opencascade.js, three,
three-mesh-bvh, three-edge-projection, dxf-writer) are bundled inside
nodejs/node_modules and ship with the exe — no external folders needed.
"""

import json
import os
import subprocess
import sys
import tempfile

from app.engine.tools.drill import DrillProposalParams

# Resolve the .cjs script path whether running from source or a frozen exe.
if getattr(sys, 'frozen', False):
    # PyInstaller extracts the whole 'nodejs/' tree next to the exe in _MEIPASS
    _BASE = sys._MEIPASS
else:
    _BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))

_SCRIPT = os.path.join(_BASE, 'nodejs', 'gen_proposal.cjs')

# Suppress the console window the subprocess would otherwise pop on Windows.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def generate(params: DrillProposalParams, out_path: str) -> None:
    """Build the proposal DXF via Node.js + OCC. Raises on failure."""
    errs = params.validate()
    if errs:
        raise ValueError("\n".join(errs))
    params.derive()

    payload = {
        "cutting_diameter": params.cutting_diameter,
        "shank_diameter":   params.effective_shank_diameter,
        "overall_length":   params.overall_length,
        "shank_length":     params.shank_length,
        "point_angle":      params.point_angle,
        "helix_angle":      params.helix_angle,
        "n_flutes":         params.n_flutes,
    }

    # Write params to a temp JSON file (avoids shell quoting issues).
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False, encoding='utf-8'
    ) as tf:
        json.dump(payload, tf)
        params_path = tf.name

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    try:
        result = subprocess.run(
            ['node', _SCRIPT, params_path, out_path],
            capture_output=True,
            text=True,
            timeout=300,
            creationflags=_NO_WINDOW,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if "is not recognized" in stderr or "ENOENT" in stderr and "node" in stderr.lower():
                raise RuntimeError(
                    "Node.js was not found on this system.\n"
                    "Install it from https://nodejs.org and try again."
                )
            raise RuntimeError(
                f"DXF generator failed (exit {result.returncode}):\n{stderr}"
            )
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("Generator ran but produced no output file.")
    except FileNotFoundError:
        # 'node' executable not on PATH
        raise RuntimeError(
            "Node.js was not found on this system.\n"
            "Install it from https://nodejs.org and try again."
        )
    finally:
        try:
            os.unlink(params_path)
        except OSError:
            pass
