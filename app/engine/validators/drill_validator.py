from app.engine.tools.drill import DrillBlankParams


def validate_blank(params: DrillBlankParams) -> list[str]:
    """Run full validation for a drill blank. Returns list of error strings."""
    params.derive()
    return params.validate()
