"""Diagnostic check interface. Implementations live in app/diagnostics/checks/ (Agent C)."""

from abc import ABC, abstractmethod

from app.models import Finding, Resource


class DiagnosticCheck(ABC):
    id: str
    name: str
    description: str

    @abstractmethod
    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        """previous is the prior snapshot's resources when one exists, for
        checks that compare across time (host count change, resource removed)."""
