"""Built-in diagnostic checks. Order here is the order run_all executes them."""

from app.diagnostics.base import DiagnosticCheck
from app.diagnostics.checks.cluster import (
    ClusterDrsDisabled,
    ClusterHaDisabled,
    ClusterHostCountChange,
    HostCountLow,
)
from app.diagnostics.checks.datastore import DatastoreHighUsage, DatastoreInaccessible
from app.diagnostics.checks.host import (
    HostDisconnected,
    HostMaintenanceMode,
    HostNotResponding,
    HostNtpNotConfigured,
    HostVersionMismatch,
)
from app.diagnostics.checks.removed import NetworkRemoved, ResourceRemoved
from app.diagnostics.checks.vm import (
    VmOrphanedOrInaccessible,
    VmPoweredOff,
    VmSnapshotStale,
    VmToolsNotRunning,
)

ALL_CHECKS: list[type[DiagnosticCheck]] = [
    HostDisconnected,
    HostNotResponding,
    HostMaintenanceMode,
    HostNtpNotConfigured,
    HostVersionMismatch,
    DatastoreHighUsage,
    DatastoreInaccessible,
    VmPoweredOff,
    VmOrphanedOrInaccessible,
    VmSnapshotStale,
    VmToolsNotRunning,
    ClusterHostCountChange,
    HostCountLow,
    ClusterHaDisabled,
    ClusterDrsDisabled,
    NetworkRemoved,
    ResourceRemoved,
]

__all__ = ["ALL_CHECKS"]
