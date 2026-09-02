"""Datastore capacity and accessibility checks."""

from app.diagnostics.base import DiagnosticCheck
from app.diagnostics.checks._common import by_type, datastore_usage_pct, make_finding
from app.models import Finding, Resource

WARN_PCT = 85.0
CRIT_PCT = 95.0


class DatastoreHighUsage(DiagnosticCheck):
    id = "DATASTORE_HIGH_USAGE"
    name = "Datastore high usage"
    description = "A datastore is above 85% (warning) or 95% (critical) used."
    resource_type = "datastore"

    def applicable(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Resource]:
        # Usage is only judged when capacity and free space are both known.
        return [ds for ds in by_type(resources, "datastore") if datastore_usage_pct(ds) is not None]

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        out: list[Finding] = []
        for ds in by_type(resources, "datastore"):
            pct = datastore_usage_pct(ds)
            if pct is None or pct < WARN_PCT:
                continue
            severity = "critical" if pct >= CRIT_PCT else "warning"
            threshold = CRIT_PCT if severity == "critical" else WARN_PCT
            out.append(
                make_finding(
                    self.id,
                    ds,
                    severity,
                    f"Datastore {ds.name} is {pct:.1f}% full",
                    f"Datastore {ds.name} is {pct:.1f}% used, above the {threshold:.0f}% "
                    "threshold. VMs on a full datastore can pause or fail to snapshot.",
                    {
                        "pct": pct,
                        "capacity": ds.properties.get("capacity"),
                        "freeSpace": ds.properties.get("freeSpace"),
                        "threshold": threshold,
                    },
                    f"Free space on {ds.name}: delete or consolidate old snapshots, remove "
                    "orphaned VMDKs, storage vMotion VMs to a less used datastore, or extend "
                    "the backing LUN/volume.",
                )
            )
        return out


class DatastoreInaccessible(DiagnosticCheck):
    id = "DATASTORE_INACCESSIBLE"
    name = "Datastore inaccessible"
    description = "A datastore is reported as inaccessible."
    resource_type = "datastore"

    def evaluate(
        self, resources: list[Resource], previous: list[Resource] | None = None
    ) -> list[Finding]:
        out: list[Finding] = []
        for ds in by_type(resources, "datastore"):
            if ds.properties.get("accessible") is False:
                out.append(
                    make_finding(
                        self.id,
                        ds,
                        "critical",
                        f"Datastore {ds.name} is inaccessible",
                        f"Datastore {ds.name} reports accessible=false. VMs stored on it cannot "
                        "run or be powered on.",
                        {"accessible": False, "capacity": ds.properties.get("capacity")},
                        f"Check the storage path to {ds.name}: verify the array/NFS export is "
                        "online, storage network and HBA/NIC links are up, and rescan storage "
                        "adapters on the affected hosts.",
                    )
                )
        return out
