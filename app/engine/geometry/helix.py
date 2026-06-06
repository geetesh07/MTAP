"""
Helix projection math for proposal drawings.

A helix on a cylindrical tool body projects as a sinusoidal curve in the
front (orthographic) view. This module will contain:
  - Helix period calculation from helix angle + diameter
  - Point-by-point projection of the 3D helix onto the 2D front plane
  - Entry and exit transition geometry at both ends of the flute

Not yet implemented — will be defined when proposal drawing mode begins.
"""

import numpy as np


def helix_period(diameter_mm: float, helix_angle_deg: float) -> float:
    """
    Axial distance for one full revolution of the helix (lead).
    lead = pi * D / tan(helix_angle)
    """
    import math
    return math.pi * diameter_mm / math.tan(math.radians(helix_angle_deg))


def project_helix_front_view(
    diameter_mm: float,
    helix_angle_deg: float,
    flute_length_mm: float,
    n_points: int = 500,
) -> np.ndarray:
    """
    Returns Nx2 array of (x, y) points representing the helix projection
    onto the front view plane. x = axial position, y = radial projection.
    Not yet implemented.
    """
    raise NotImplementedError("Helix projection will be implemented in proposal mode.")
