import math
from dataclasses import dataclass, field
from app.engine.tools.base_tool import BaseTool

# Numerical tolerance for treating two dimensions as equal (mm / deg).
EPS = 1e-6


@dataclass
class DrillBlankParams(BaseTool):
    """
    Parameters and derived geometry for a twist drill BLANK drawing.

    A blank is the raw ground stock before flutes are cut. This class holds the
    user inputs AND computes the pure geometry (axial stations + profile points).
    It contains NO DXF code — the DXF writer consumes this output. This keeps the
    parametric engine cleanly separated from the drawing layer.

    All linear dimensions in millimetres, all angles in degrees.

    Construction convention (see memory 'blank-construction'):
      Centerline on y=0. Origin x=0 at the LEFT / cutting (point) end.
      Build LEFT -> RIGHT toward the shank backface at x=OAL.
      Axial identity:  OAL = point_length + body_length + reinforcement_length + shank_length
      where body_length is the full-diameter (Dc) cylindrical section that hosts
      the flute. flute_length is a sub-region of body_length (default = full body).
    """

    # --- Primary inputs (user-supplied) ---
    tool_type: str = "Drill"
    cutting_diameter: float = 0.0        # Dc — diameter of the cutting/flute end (left)
    shank_diameter: float = 0.0          # D  — diameter of the shank (right)
    overall_length: float = 0.0          # OAL — total tip-to-backface length
    shank_length: float = 0.0            # Ls — axial length of the shank section

    reinforcement: bool = False          # True = conical transition between Dc and D
    reinforcement_angle: float = 30.0    # Half-angle from the centerline (deg). Used only
                                         # when reinforcement is on AND Dc != D.

    point_angle: float = 140.0           # Full included angle of the point cone (deg).
                                         # 180 = flat cutting face (no cone).

    # User may shorten the flute below the available body length (leaves a plain
    # parallel land). None => auto = full body_length (the maximum that fits).
    flute_length_override: float | None = None

    # Back taper: the slight reduction in body diameter per unit length for cutting
    # clearance. Stored in mm/100mm (i.e. per 100 mm of length). 0 = no annotation.
    back_taper: float = 0.0

    # GD&T runout tolerance for the cutting diameter (annotated on the drawing).
    # 0 = suppress the GD&T block.
    runout: float = 0.010

    # --- Derived (computed in derive()) ---
    point_length: float = field(default=0.0, init=False)          # axial height of point cone
    reinforcement_length: float = field(default=0.0, init=False)  # axial length of transition cone
    body_length: float = field(default=0.0, init=False)           # full-Dc cylindrical length available
    flute_length: float = field(default=0.0, init=False)          # effective flute length (<= body_length)

    # ----------------------------------------------------------------- helpers
    @property
    def diameters_differ(self) -> bool:
        return abs(self.shank_diameter - self.cutting_diameter) > EPS

    @property
    def has_transition_cone(self) -> bool:
        """A sloped reinforcement cone is drawn only when enabled AND diameters differ."""
        return self.reinforcement and self.diameters_differ

    # axial stations (valid after derive())
    @property
    def x_point_base(self) -> float:
        return self.point_length

    @property
    def x_body_end(self) -> float:
        return self.point_length + self.body_length

    @property
    def x_shank_start(self) -> float:
        return self.x_body_end + self.reinforcement_length

    @property
    def x_end(self) -> float:
        return self.overall_length

    # ------------------------------------------------------------------ derive
    def derive(self) -> None:
        Dc = self.cutting_diameter
        D = self.shank_diameter

        # Point cone axial height: half-base / tan(half included angle). 180 -> flat.
        if self.point_angle >= 180.0 - EPS or self.point_angle <= EPS:
            self.point_length = 0.0
        else:
            self.point_length = (Dc / 2.0) / math.tan(math.radians(self.point_angle / 2.0))

        # Reinforcement (transition) cone axial length. Angle is measured FROM the
        # centerline, so tan(angle) = radial_drop / axial_length.
        if self.has_transition_cone and 0.0 < self.reinforcement_angle < 90.0:
            radial_drop = abs(D - Dc) / 2.0
            self.reinforcement_length = radial_drop / math.tan(math.radians(self.reinforcement_angle))
        else:
            # No reinforcement (abrupt step) or equal diameters -> zero axial length.
            self.reinforcement_length = 0.0

        # Remaining length forms the full-diameter (Dc) cylindrical body.
        self.body_length = (
            self.overall_length
            - self.point_length
            - self.reinforcement_length
            - self.shank_length
        )

        # Effective flute length: default = full body; user override is clamped to body.
        if self.flute_length_override is None:
            self.flute_length = max(self.body_length, 0.0)
        else:
            self.flute_length = min(max(self.flute_length_override, 0.0), max(self.body_length, 0.0))

    # ---------------------------------------------------------------- validate
    def validate(self) -> list[str]:
        errors: list[str] = []

        # ── Basic presence checks ────────────────────────────────────
        if self.cutting_diameter <= 0:
            errors.append("Cutting diameter (Dc) must be greater than 0.")
        if self.shank_diameter <= 0:
            errors.append("Shank diameter (D) must be greater than 0.")
        if self.overall_length <= 0:
            errors.append("Overall length (OAL) must be greater than 0.")
        if self.shank_length <= 0:
            errors.append("Shank length (Ls) must be greater than 0.")
        if not (0 < self.point_angle <= 180):
            errors.append("Point angle must be between 0° and 180°.")
        if self.has_transition_cone and not (0 < self.reinforcement_angle < 90):
            errors.append("Reinforcement angle must be between 0° and 90° (from centerline).")

        # ── Solid-model guarantee: parts must fit inside OAL ─────────
        if self.body_length < -EPS:
            deficit = -self.body_length
            errors.append(
                f"Point + reinforcement + shank exceed OAL by {deficit:.3f} mm. "
                f"Increase OAL or reduce shank/point/reinforcement."
            )

        if self.flute_length_override is not None and self.flute_length_override > self.body_length + EPS:
            errors.append(
                f"Flute length ({self.flute_length_override:.3f}) exceeds available body "
                f"length ({max(self.body_length, 0.0):.3f} mm)."
            )

        # ── Manufacturing constraints (industry rules for twist drills) ──
        # Only checked when primary inputs are valid (positive values).
        Dc = self.cutting_diameter
        D  = self.shank_diameter
        if Dc > 0 and D > 0 and self.overall_length > 0 and self.shank_length > 0:

            # L/D ratio (OAL / Dc)
            # Stub ~3:1, Standard 5–8:1, Long series up to 12:1,
            # Extra-long up to 20:1. Beyond 20:1 requires special support/guiding.
            ld = self.overall_length / Dc
            if ld > 20:
                errors.append(
                    f"L/D ratio {ld:.1f}:1 (OAL/Dc) exceeds 20:1 — extra-long range. "
                    f"Special guiding/support bushings required."
                )
            elif ld > 12:
                errors.append(
                    f"L/D ratio {ld:.1f}:1 (OAL/Dc) is long-series (>12:1). "
                    f"Ensure adequate guiding or bushing in setup."
                )

            # Minimum shank length: Ls ≥ 2×Dc for secure chuck/collet grip.
            if self.shank_length < 2.0 * Dc:
                errors.append(
                    f"Shank length {self.shank_length:.3f} mm < 2×Dc ({2*Dc:.3f} mm). "
                    f"Minimum for secure clamping is 2×Dc."
                )

            # Point angle practical range: 60°–160° for twist drills.
            # 118° standard HSS; 135–140° for hard/abrasive materials.
            if 0 < self.point_angle < 60:
                errors.append(
                    f"Point angle {self.point_angle:.1f}° < 60° — impractical for twist drills. "
                    f"Typical range: 60°–160°."
                )
            elif 160 < self.point_angle < 180 - EPS:
                errors.append(
                    f"Point angle {self.point_angle:.1f}° > 160° — very obtuse; "
                    f"cutting efficiency drops sharply above 160°."
                )

            # Shank > 1.5×Dc is unusual (mismatch or wrong input).
            if D > Dc * 1.5:
                errors.append(
                    f"Shank D={D:.3f} mm > 1.5×Dc ({Dc:.3f} mm) — verify intentional; "
                    f"reinforcement transition recommended."
                )

            # Minimum body (flute) length: 1×Dc for chip clearance.
            if self.body_length > -EPS and self.body_length < Dc - EPS:
                errors.append(
                    f"Body length {max(self.body_length, 0):.3f} mm < Dc ({Dc:.3f} mm). "
                    f"Chip clearance insufficient; minimum recommended is 1×Dc."
                )

            # Back taper: 0.01–0.08 mm/100mm typical HSS; > 0.15 unusual.
            if self.back_taper > 0.15:
                errors.append(
                    f"Back taper {self.back_taper:.3f} mm/100mm > 0.15 mm/100mm "
                    f"(typical max for standard drills is 0.08 mm/100mm)."
                )

        return errors

    # ------------------------------------------------------------ profile geom
    def profile_points(self) -> list[tuple[float, float]]:
        """
        Closed outline of the blank (one polyline), top profile then mirrored
        bottom, traversed clockwise from the point end. Consumed by the DXF writer.
        Handles: flat point (180°), sloped reinforcement, abrupt step, straight body.
        """
        rc = self.cutting_diameter / 2.0   # cutting radius
        rs = self.shank_diameter / 2.0     # shank radius

        x1 = self.x_point_base
        x2 = self.x_body_end
        x3 = self.x_shank_start
        x4 = self.x_end

        pts: list[tuple[float, float]] = []

        # --- top profile, left -> right ---
        if self.point_length > EPS:
            pts.append((0.0, 0.0))     # cone apex
            pts.append((x1, rc))       # cone base (top)
        else:
            pts.append((0.0, rc))      # flat cutting face (top corner)

        pts.append((x2, rc))           # end of Dc body (top)
        pts.append((x3, rs))           # transition top (slope, step, or colinear)
        pts.append((x4, rs))           # shank to backface (top)

        # --- backface, then bottom profile right -> left ---
        pts.append((x4, -rs))          # backface (full shank diameter)
        pts.append((x3, -rs))          # shank start (bottom)
        pts.append((x2, -rc))          # transition bottom
        if self.point_length > EPS:
            pts.append((x1, -rc))      # cone base (bottom); closes back to apex
        else:
            pts.append((0.0, -rc))     # flat cutting face (bottom corner)

        return _dedupe(pts)


def _dedupe(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove consecutive duplicate vertices (can occur when a section has 0 length)."""
    out: list[tuple[float, float]] = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > EPS or abs(p[1] - out[-1][1]) > EPS:
            out.append(p)
    return out


@dataclass
class DrillProposalParams(BaseTool):
    """Full proposal drawing parameters — defined when proposal mode is implemented."""

    def validate(self) -> list[str]:
        return ["Proposal parameters not yet defined."]

    def derive(self) -> None:
        pass
