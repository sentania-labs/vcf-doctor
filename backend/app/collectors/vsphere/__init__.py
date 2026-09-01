"""Live vCenter collector (Agent B).

    VSphereCollector(host, username, password, verify_tls=False)

test_connection() never raises; it returns ConnectionResult(ok=False, ...)
for unreachable hosts, bad credentials and TLS failures. collect() raises
VSphereError (or a subclass) with an operator-readable message.

See normalize.py for the ID scheme and the property list.
"""

from __future__ import annotations

import logging

from app.collectors.base import Collector
from app.collectors.vsphere.client import (
    VSphereAuthError,
    VSphereError,
    VSphereSession,
    VSphereTLSError,
    VSphereUnreachableError,
    classify_exception,
)
from app.collectors.vsphere.normalize import (
    RawInventory,
    RawObject,
    normalize,
    vcenter_key,
)
from app.models import ConnectionResult, Resource

log = logging.getLogger(__name__)

RESOURCE_TYPES = ["vcenter", "datacenter", "cluster", "host", "vm", "datastore", "network"]


class VSphereCollector(Collector):
    resource_types = RESOURCE_TYPES

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_tls: bool = False,
        namespace: str | None = None,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.verify_tls = verify_tls
        self.namespace = namespace or vcenter_key(host)
        self.id = f"vcenter:{self.namespace}"

    def _session(self) -> VSphereSession:
        return VSphereSession(self.host, self.username, self.password, self.verify_tls)

    def test_connection(self) -> ConnectionResult:
        try:
            with self._session() as s:
                about = s.about()
        except VSphereError as exc:
            log.info("vsphere test_connection %s failed: %s", self.host, exc)
            return ConnectionResult(ok=False, message=str(exc))
        except Exception as exc:  # noqa: BLE001  never let test_connection raise
            err = classify_exception(exc, self.host)
            log.warning("vsphere test_connection %s unexpected: %s", self.host, err)
            return ConnectionResult(ok=False, message=str(err))
        version = about.get("version")
        build = about.get("build")
        label = about.get("fullName") or about.get("name") or self.host
        return ConnectionResult(
            ok=True,
            message=f"Connected to {label}",
            version=version,
            build=build,
        )

    def collect(self) -> list[Resource]:
        try:
            with self._session() as s:
                inventory = s.inventory()
        except VSphereError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise classify_exception(exc, self.host) from exc
        resources = normalize(inventory, self.namespace)
        log.info("vsphere collect %s: %d resources", self.host, len(resources))
        return resources


__all__ = [
    "RESOURCE_TYPES",
    "RawInventory",
    "RawObject",
    "VSphereAuthError",
    "VSphereCollector",
    "VSphereError",
    "VSphereTLSError",
    "VSphereUnreachableError",
    "normalize",
    "vcenter_key",
]
