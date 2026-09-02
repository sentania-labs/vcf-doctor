"""Diagnostic check interface. Implementations live in app/diagnostics/checks/ (Agent C)."""

from abc import ABC, abstractmethod

from app.models import Finding, Resource


class DiagnosticCheck(ABC):
    id: str
    name: str
    description: str
    # Resource type this check looks at. The default applicable() uses it to
    # report how many objects the check considered, which the health score
    # divides by. None means every resource.
    resource_type: str | None = None

    @abstractmethod
    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        """previous is the prior snapshot's resources when one exists, for
        checks that compare across time (host count change, resource removed)."""

    def applicable(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Resource]:
        """Objects this check actually judged on this snapshot. An empty list
        means the check did not evaluate anything (nothing of that type, or
        the data it needs was not collected), and the Overview reports it as
        "not evaluated" rather than "passed". Override when the check skips
        objects that lack the property it inspects."""
        if self.resource_type is None:
            return list(resources)
        return [r for r in resources if r.type == self.resource_type]
