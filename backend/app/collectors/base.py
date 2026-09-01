"""Collector interface. Implementations: fixture (Agent A), vsphere (Agent B)."""

from abc import ABC, abstractmethod

from app.models import ConnectionResult, Resource


class Collector(ABC):
    id: str
    resource_types: list[str]

    @abstractmethod
    def test_connection(self) -> ConnectionResult: ...

    @abstractmethod
    def collect(self) -> list[Resource]: ...
