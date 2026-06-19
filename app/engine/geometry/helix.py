"""
Exact parametric curves for the 2D side-view projection of helical flute grooves.

Derivation
----------
The flute cutter is a circle of radius rho swept along a helix of radius R on the
drill body (rho = 0.3 * Dc, matching occDrill.ts).  The swept tube intersects the
body cylinder at two groove-edge curves.  For each helix angle theta the intersection
occurs at cutter circle angle alpha = +-alpha_star, where cos(alpha_star) is the
smaller real root of:

    R^2 * cos^2(alpha) - (2*R*M^2/rho) * cos(alpha) + L^2 = 0
    M = sqrt(R^2 + L^2),  L = lead / (2*pi) = R / tan(helix_angle)

The projected (x_drawing=axial, y_drawing=radial) curves are then parametric
sinusoids -- not approximations; this is the exact closed-form result matching
what OpenCASCADE computes via B-rep boolean cut + edge extraction.

Groove edge curves (two per flute):
    x(theta) = theta*L +- K_z   (small axial offset)
    y(theta) = A*cos(theta+phi) +- B*sin(theta+phi)
    where A = R - rho*cos(alpha_star)
          B = rho*L*sin(alpha_star)/M
          K_z = rho*R*sin(alpha_star)/M

Groove floor curve (one per flute, pure sinusoid):
    x(theta) = theta*L
    y(theta) = (R - rho)*cos(theta + phi)
"""

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class _FluteGeom:
    R: float
    rho: float
    L: float
    M: float
    cos_star: float
    sin_star: float
    A: float
    B: float
    K_z: float
    r_floor: float   # R - rho


def _compute_geom(Dc: float, helix_deg: float, rho_ratio: float = 0.3) -> _FluteGeom | None:
    R   = Dc / 2.0
    rho = rho_ratio * Dc
    if helix_deg <= 0 or helix_deg >= 90:
        return None
    lead = math.pi * Dc / math.tan(math.radians(helix_deg))
    L    = lead / (2.0 * math.pi)
    M    = math.hypot(R, L)

    # Solve: R^2 c^2 - (2*R*M^2/rho)*c + L^2 = 0
    a_q = R * R
    b_q = -2.0 * R * M * M / rho
    c_q = L * L
    disc = b_q * b_q - 4.0 * a_q * c_q
    if disc < 0:
        return None

    # Take the smaller root (the one with |cos| <= 1)
    sqrt_disc = math.sqrt(disc)
    c1 = (-b_q - sqrt_disc) / (2.0 * a_q)   # smaller
    c2 = (-b_q + sqrt_disc) / (2.0 * a_q)   # larger

    cos_star = None
    for c_val in (c1, c2):
        if -1.0 - 1e-9 <= c_val <= 1.0 + 1e-9:
            cos_star = max(-1.0, min(1.0, c_val))
            break
    if cos_star is None:
        return None

    sin_star = math.sqrt(max(0.0, 1.0 - cos_star * cos_star))
    A   = R  - rho * cos_star
    B   = rho * L * sin_star / M
    K_z = rho * R * sin_star / M

    return _FluteGeom(R=R, rho=rho, L=L, M=M,
                      cos_star=cos_star, sin_star=sin_star,
                      A=A, B=B, K_z=K_z, r_floor=R - rho)


def flute_curves(
    Dc: float,
    helix_deg: float,
    n_flutes: int,
    x_flute_start: float,
    x_flute_end: float,
    n_points: int = 300,
    rho_ratio: float = 0.3,
) -> list[dict]:
    """
    Return a list of curve dicts for the side-view projection of all flute grooves.

    Each dict:
        pts   : list of (x, y) tuples  (x = axial, y = radial)
        kind  : 'edge' | 'floor'
        flute : flute index 0..n_flutes-1

    The 'edge' curves are the outer groove boundaries (the braid/weave pattern).
    The 'floor' curves show the inner groove depth.
    """
    geom = _compute_geom(Dc, helix_deg, rho_ratio)
    if geom is None or x_flute_end <= x_flute_start:
        return []

    span = x_flute_end - x_flute_start
    curves: list[dict] = []

    for i in range(n_flutes):
        phi = 2.0 * math.pi * i / n_flutes

        # ── Groove floor (alpha = 0, minimum radius point, pure sinusoid) ──────
        xs = np.linspace(x_flute_start, x_flute_end, n_points)
        theta = (xs - x_flute_start) / geom.L
        ys = geom.r_floor * np.cos(theta + phi)
        curves.append({
            "pts":   list(zip(xs.tolist(), ys.tolist())),
            "kind":  "floor",
            "flute": i,
        })

        # ── Groove edge +alpha_star ──────────────────────────────────────────────
        # x_draw = theta*L + K_z + x_flute_start  →  theta = (x_draw - K_z - x_start)/L
        xs1 = np.linspace(x_flute_start + geom.K_z, x_flute_end + geom.K_z, n_points)
        theta1 = (xs1 - geom.K_z - x_flute_start) / geom.L
        ys1 = geom.A * np.cos(theta1 + phi) + geom.B * np.sin(theta1 + phi)
        # clip to declared flute zone (K_z offset is small, < 2 mm normally)
        mask1 = (xs1 >= x_flute_start) & (xs1 <= x_flute_end)
        if mask1.any():
            curves.append({
                "pts":   list(zip(xs1[mask1].tolist(), ys1[mask1].tolist())),
                "kind":  "edge",
                "flute": i,
            })

        # ── Groove edge -alpha_star ──────────────────────────────────────────────
        xs2 = np.linspace(x_flute_start - geom.K_z, x_flute_end - geom.K_z, n_points)
        theta2 = (xs2 + geom.K_z - x_flute_start) / geom.L
        ys2 = geom.A * np.cos(theta2 + phi) - geom.B * np.sin(theta2 + phi)
        mask2 = (xs2 >= x_flute_start) & (xs2 <= x_flute_end)
        if mask2.any():
            curves.append({
                "pts":   list(zip(xs2[mask2].tolist(), ys2[mask2].tolist())),
                "kind":  "edge",
                "flute": i,
            })

    return curves


def helix_lead(Dc: float, helix_deg: float) -> float:
    """Axial distance per full revolution (mm)."""
    return math.pi * Dc / math.tan(math.radians(helix_deg))
