# MTAP — Machine Tool Automation Program

Parametric CAD drawing software for cutting tools (drills, end mills, reamers).
Takes tool parameters and generates accurate, to-scale engineering drawings as
DXF (and PDF). Built with Python + PyQt6.

## Setup

```powershell
python -m pip install -r requirements.txt
python main.py
```

The window launches maximized to the **Mode Selector**.

## Modes

| Mode | Status | Purpose |
|------|--------|---------|
| **Blank Drawing** | ✅ Active | Raw ground stock before fluting — shank, point, reinforcement, diameters |
| **Proposal Drawing** | In progress | Full to-scale tool drawing with helix (customer approval) |
| **Production Drawing** | In progress | Shop-floor drawing with GD&T, surface finish, inspection dims |

## Blank Drawing

Inputs: tool type, cutting diameter (Dc), shank diameter (D), overall length
(OAL), shank length (Ls), point angle (180° = flat), optional shank
reinforcement (with angle measured from the centerline). Flute length is derived
(`OAL − point − reinforcement − shank`) and may be shortened by the user.

- Live preview updates as you type.
- **Export DXF** — 1:1 model-scale DXF, opens in AutoCAD (layered: OUTLINE,
  CENTERLINE, DIMENSION, ANNOTATION).
- **Export PDF** — rendered drawing on white for printing.

Geometry handles all cases: equal diameters (straight), Dc<D and Dc>D, with a
sloped reinforcement cone or an abrupt step. Inputs that can't fit inside OAL are
rejected with a clear message before any drawing is produced.

## Architecture

```
app/
  engine/   # pure geometry + validation (no DXF/UI knowledge)
  dxf/      # DXF entity writers + PDF/PNG rendering
  ui/       # PyQt6 screens + QSS theme
  utils/    # config / constants
```

The parametric engine, DXF writers, and UI are kept strictly separate so each
tool type and mode can grow independently.

## Roadmap

- Shank backface geometry (pending spec)
- Title block import from per-client AutoCAD DXF templates
- Proposal mode + helix projection
- Production mode (GD&T)
- STEP output via cadquery
