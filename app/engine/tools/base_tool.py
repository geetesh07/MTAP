from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class BaseTool(ABC):
    """Abstract base for all cutting tool parameter classes."""

    @abstractmethod
    def validate(self) -> list[str]:
        """Return list of validation error strings. Empty list = valid."""
        ...

    @abstractmethod
    def derive(self) -> None:
        """Compute any derived/calculated dimensions from the primary inputs."""
        ...
