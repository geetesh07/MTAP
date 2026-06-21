r"""
Pure-function unit tests for MTAP parametric engine.
No OCC, AutoCAD, Node.js or GUI required -- runs in plain pytest.

Run with:  cd E:\Geetesh\MTAP && python -m pytest tests/ -v
"""
import math
import sys
import os
import types
import unittest.mock as mock

# Ensure project root is on sys.path so imports resolve without installation.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ─── 1. Axial identity: OAL = sum of all sections ─────────────────────────────

def test_derive_axial_identity_no_reinforcement():
    from app.engine.tools.drill import DrillProposalParams
    p = DrillProposalParams(
        cutting_diameter=10, shank_diameter=10, overall_length=100,
        shank_length=40, point_angle=118, helix_angle=30, n_flutes=2,
    )
    p.derive()
    total = p.point_length + p.body_length + p.reinforcement_length + p.shank_length
    assert abs(total - p.overall_length) < 1e-9, (
        f"Axial identity broken: {p.point_length:.4f} + {p.body_length:.4f} + "
        f"{p.reinforcement_length:.4f} + {p.shank_length:.4f} = {total:.4f} "
        f"but OAL = {p.overall_length:.4f}")


def test_derive_axial_identity_with_reinforcement():
    from app.engine.tools.drill import DrillProposalParams
    p = DrillProposalParams(
        cutting_diameter=10, shank_diameter=16, overall_length=120,
        shank_length=45, point_angle=140, helix_angle=30, n_flutes=2,
        reinforcement=True, reinforcement_angle=30,
    )
    p.derive()
    total = p.point_length + p.body_length + p.reinforcement_length + p.shank_length
    assert abs(total - p.overall_length) < 1e-9, (
        f"Axial identity with reinforcement broken: total={total:.4f} OAL={p.overall_length:.4f}")


# ─── 2. validate() boundary table ─────────────────────────────────────────────

def test_validate_detects_negative_body():
    from app.engine.tools.drill import DrillProposalParams
    p = DrillProposalParams(
        cutting_diameter=10, shank_diameter=10, overall_length=30,
        shank_length=40, point_angle=118, helix_angle=30, n_flutes=2,
    )
    p.derive()
    errs = p.validate()
    assert any("OAL" in e or "exceed" in e.lower() or "body" in e.lower() for e in errs), (
        f"Expected body/OAL overflow error, got: {errs}")


def test_validate_passes_nominal():
    from app.engine.tools.drill import DrillProposalParams
    p = DrillProposalParams(
        cutting_diameter=10, shank_diameter=10, overall_length=100,
        shank_length=40, point_angle=118, helix_angle=30, n_flutes=2,
    )
    p.derive()
    errs = p.validate()
    assert errs == [], f"Expected no errors for nominal drill, got: {errs}"


def test_validate_zero_diameter():
    from app.engine.tools.drill import DrillProposalParams
    p = DrillProposalParams(
        cutting_diameter=0, shank_diameter=10, overall_length=100,
        shank_length=40, point_angle=118, helix_angle=30, n_flutes=2,
    )
    p.derive()
    errs = p.validate()
    assert any("Cutting diameter" in e or "Dc" in e for e in errs), (
        f"Expected Dc error for zero diameter, got: {errs}")


# ─── 3. _lstr injection escaping ──────────────────────────────────────────────

def test_lstr_escapes_backslash_and_quote():
    from app.dxf.lsp_writer import _lstr
    result = _lstr(r'path\to\"dir"')
    # Must not contain a bare backslash or bare double-quote inside the string
    inner = result[1:-1]  # strip surrounding quotes
    assert '\\"' in inner or "\\\\" in inner, f"Escaping missing in: {result}"
    # Must not contain unescaped " (would break LISP string literal)
    # Every " inside must be preceded by \
    i = 0
    while i < len(inner):
        if inner[i] == '\\':
            i += 2  # skip escaped char
            continue
        assert inner[i] != '"', f"Unescaped quote at pos {i} in: {result}"
        i += 1


def test_lstr_newline_becomes_paragraph_code():
    from app.dxf.lsp_writer import _lstr
    result = _lstr("line one\nline two")
    assert "\\P" in result, f"Newline not converted to MTEXT \\P in: {result}"
    assert "\n" not in result, f"Raw newline leaked into LISP string: {result}"


def test_lstr_none_becomes_empty():
    from app.dxf.lsp_writer import _lstr
    assert _lstr(None) == '""'


# ─── 4. _verify_output post-condition (mocked ezdxf) ─────────────────────────

def test_verify_output_raises_when_template_missing(tmp_path):
    """_verify_output must raise RuntimeError when MTAP_TEMPLATE INSERT is absent."""
    fake_dxf = tmp_path / "out.dxf"
    fake_dxf.write_text("placeholder")

    mock_entity = mock.MagicMock()
    mock_entity.dxftype.return_value = "INSERT"
    mock_entity.dxf.name = "SOME_OTHER_BLOCK"

    mock_msp = [mock_entity]
    mock_doc = mock.MagicMock()
    mock_doc.modelspace.return_value = mock_msp

    with mock.patch("app.dxf.proposal_acad.ezdxf.readfile", return_value=mock_doc):
        from app.dxf.proposal_acad import _verify_output
        try:
            _verify_output(str(fake_dxf), require_gdt=False)
            assert False, "_verify_output should have raised RuntimeError"
        except RuntimeError as exc:
            assert "MTAP_TEMPLATE" in str(exc), f"Wrong error message: {exc}"


def test_verify_output_passes_when_template_present(tmp_path):
    """_verify_output must NOT raise when MTAP_TEMPLATE INSERT is present."""
    fake_dxf = tmp_path / "out.dxf"
    fake_dxf.write_text("placeholder")

    def make_insert(name):
        e = mock.MagicMock()
        e.dxftype.return_value = "INSERT"
        e.dxf.name = name
        return e

    mock_msp = [make_insert("MTAP_TEMPLATE")]
    mock_doc = mock.MagicMock()
    mock_doc.modelspace.return_value = mock_msp

    with mock.patch("app.dxf.proposal_acad.ezdxf.readfile", return_value=mock_doc):
        from app.dxf.proposal_acad import _verify_output
        _verify_output(str(fake_dxf), require_gdt=False)  # must not raise


# ─── 5. profile_points() invariants (DrillBlankParams, pure Python) ───────────

def test_profile_points_tip_is_at_origin():
    from app.engine.tools.drill import DrillBlankParams
    p = DrillBlankParams(
        cutting_diameter=10, shank_diameter=10, overall_length=100,
        shank_length=40, point_angle=118,
    )
    p.derive()
    pts = p.profile_points()
    # First point is the apex on the axis (y=0) for a pointed drill
    assert pts[0] == (0.0, 0.0), f"Expected apex at origin, got {pts[0]}"


def test_profile_points_symmetry():
    from app.engine.tools.drill import DrillBlankParams
    p = DrillBlankParams(
        cutting_diameter=12, shank_diameter=16, overall_length=120,
        shank_length=45, point_angle=140, reinforcement=True, reinforcement_angle=30,
    )
    p.derive()
    pts = p.profile_points()
    # Profile must be symmetric: for every (x, y) there should be (x, -y)
    top    = {(round(x, 6), round(abs(y), 6)) for x, y in pts if y >= 0}
    bottom = {(round(x, 6), round(abs(y), 6)) for x, y in pts if y <= 0}
    assert top == bottom, f"Profile not symmetric. Top: {top}  Bottom: {bottom}"


def test_profile_points_backface_x_equals_oal():
    from app.engine.tools.drill import DrillBlankParams
    p = DrillBlankParams(
        cutting_diameter=10, shank_diameter=10, overall_length=100,
        shank_length=35, point_angle=118,
    )
    p.derive()
    pts = p.profile_points()
    max_x = max(x for x, _ in pts)
    assert abs(max_x - p.overall_length) < 1e-6, (
        f"Backface x={max_x} != OAL={p.overall_length}")
