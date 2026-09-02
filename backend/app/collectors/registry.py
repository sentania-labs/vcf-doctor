"""Pick the collector for a connection."""

from app.collectors.base import Collector
from app.collectors.fixture import FixtureCollector
from app.config import settings
from app.models import Connection


class CollectorUnavailable(Exception):
    """Raised when the live collector cannot be loaded. API maps this to 503."""


class CredentialsUnreadable(Exception):
    """The stored password is encrypted under a key this deployment no longer
    has. Scans and tests fail with this message until the operator re-enters
    the password on the Connections page."""


def get_collector(connection: Connection) -> Collector:
    if connection.credentials_unreadable:
        raise CredentialsUnreadable(
            "stored password cannot be decrypted with the current encryption key; "
            "re-enter the password for this connection"
        )
    if connection.kind == "fixture":
        if not settings.test_fixtures:
            # A leftover fixture connection (from the retired demo mode) must
            # not keep producing made-up inventory on a live deployment.
            raise CollectorUnavailable(
                "fixture connections are for tests only; delete this connection"
            )
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
