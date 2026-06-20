"""
proposal_dxf.py — Proposal Drawing DXF generator.

PENDING IMPLEMENTATION: block-based helix approach.
The generator will:
  1. Load a pre-drawn DXF block matching the requested helix angle
     (e.g. blocks/helix_30.dxf for 30° helix)
  2. Insert it scaled to the actual Dc and flute length
  3. Add body outline, tip cone, shank, and title block around it

This replaces the previous Node.js + OpenCASCADE pipeline.
"""

from app.engine.tools.drill import DrillProposalParams


def generate(params: DrillProposalParams, out_path: str) -> None:
    raise NotImplementedError(
        "Proposal DXF generation is not yet implemented.\n"
        "The block-based helix pipeline is under construction."
    )
