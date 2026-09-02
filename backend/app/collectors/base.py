"""Collector interface. Implementations: vsphere (live) and fixture (test-only)."""

from abc import ABC, abstractmethod

from app.models import ConnectionResult, Resource


class Collector(ABC):
    id: str
    resource_types: list[str]

    @abstractmethod
    def test_connection(self) -> ConnectionResult: ...

    @abstractmethod
    def collect(self) -> list[Resource]: ...
