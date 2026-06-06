# End mill parameter classes — stub for future implementation.
from app.engine.tools.base_tool import BaseTool
from dataclasses import dataclass


@dataclass
class EndMillBlankParams(BaseTool):
    def validate(self) -> list[str]:
        return ["End mill blank not yet implemented."]

    def derive(self) -> None:
        pass
