"""Pick the collector for a connection."""

from app.collectors.base import Collector
from app.collectors.fixture import FixtureCollector
from app.models import Connection


class CollectorUnavailable(Exception):
    """Raised when the live collector cannot be loaded. API maps this to 503."""


def get_collector(connection: Connection) -> Collector:
    if connection.kind == "fixture":
        from app.snapshots import store

        return FixtureCollector(connection.id, sequence=store.count_snapshots(connection.id))
    try:
        from app.collectors.vsphere import VSphereCollector
    except ImportError as exc:
        raise CollectorUnavailable(
            "vSphere collector is not available in this build: " + str(exc)
        ) from exc
    return VSphereCollector(
        host=connection.host,
        username=connection.username,
        password=connection.password,
        verify_tls=connection.verify_tls,
        namespace=connection.id,
    )
