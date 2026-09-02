"""Fixture collector: bundled snapshot data for tests, no vCenter needed.

Test-only: a "fixture" connection is accepted only with the
VCF_DOCTOR_TEST_FIXTURES hook (see api/router._check_kind).

The first collect for a connection returns fixtures/snapshot_a.json (healthy),
every later collect returns fixtures/snapshot_b.json (degraded). Either file
may be a JSON list of Resource objects or {"resources": [...]}. When the files
are missing a tiny built-in inventory is used so the app always boots.
"""

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.collectors.base import Collector
from app.models import ConnectionResult, Relationship, Resource
from app.models.event import Event

EVENTS_FIXTURE = "events_b.json"
_OFFSET_RE = re.compile(r"^([+-])?(\d{1,3}):(\d{2}):(\d{2})$")


def fixtures_dir() -> Path | None:
    candidates = []
    env = os.environ.get("VCF_DOCTOR_FIXTURES_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).resolve().parents[3] / "fixtures")
    candidates.append(Path("/app/fixtures"))
    candidates.append(Path.cwd() / "fixtures")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def parse_resources(payload) -> list[Resource]:
    if isinstance(payload, dict):
        payload = payload.get("resources", [])
    return [Resource.model_validate(item) for item in payload]


def load_fixture(name: str) -> list[Resource] | None:
    base = fixtures_dir()
    if base is None:
        return None
    path = base / name
    if not path.is_file():
        return None
    with path.open() as fh:
        return parse_resources(json.load(fh))


def builtin_resources(degraded: bool = False) -> list[Resource]:
    src = "vcenter:demo-vc01"
    rel = Relationship
    host2_state = "disconnected" if degraded else "connected"
    vm3_power = "poweredOff" if degraded else "poweredOn"
    return [
        Resource(
            id="vcenter:demo-vc01",
            type="vcenter",
            name="demo-vc01",
            source=src,
            properties={"version": "8.0.3", "build": "24022515"},
        ),
        Resource(
            id="datacenter:demo-vc01:dc01",
            type="datacenter",
            name="dc01",
            source=src,
            parent_id="vcenter:demo-vc01",
        ),
        Resource(
            id="cluster:demo-vc01:wld01-cl01",
            type="cluster",
            name="wld01-cl01",
            source=src,
            parent_id="datacenter:demo-vc01:dc01",
            properties={"haEnabled": True, "drsEnabled": True, "hostCount": 2},
            relationships=[rel(kind="member_of", target_id="datacenter:demo-vc01:dc01")],
        ),
        Resource(
            id="host:demo-vc01:esx01",
            type="host",
            name="esx01",
            source=src,
            parent_id="cluster:demo-vc01:wld01-cl01",
            properties={
                "connectionState": "connected",
                "powerState": "poweredOn",
                "maintenanceMode": False,
                "version": "8.0.3",
            },
            relationships=[rel(kind="member_of", target_id="cluster:demo-vc01:wld01-cl01")],
        ),
        Resource(
            id="host:demo-vc01:esx02",
            type="host",
            name="esx02",
            source=src,
            parent_id="cluster:demo-vc01:wld01-cl01",
            properties={
                "connectionState": host2_state,
                "powerState": "poweredOn",
                "maintenanceMode": False,
                "version": "8.0.3",
            },
            relationships=[rel(kind="member_of", target_id="cluster:demo-vc01:wld01-cl01")],
        ),
        Resource(
            id="datastore:demo-vc01:vsanDatastore",
            type="datastore",
            name="vsanDatastore",
            source=src,
            parent_id="datacenter:demo-vc01:dc01",
            properties={
                "type": "vsan",
                "capacity": 4_000_000_000_000,
                "freeSpace": 1_200_000_000_000 if degraded else 2_800_000_000_000,
                "accessible": True,
            },
        ),
        Resource(
            id="network:demo-vc01:seg-app",
            type="network",
            name="seg-app",
            source=src,
            parent_id="datacenter:demo-vc01:dc01",
            properties={"type": "nsx-segment", "vlan": None},
        ),
        Resource(
            id="vm:demo-vc01:app01",
            type="vm",
            name="app01",
            source=src,
            parent_id="host:demo-vc01:esx01",
            properties={
                "powerState": "poweredOn",
                "cpu": 4,
                "memoryMB": 8192,
                "toolsStatus": "toolsOk",
            },
            relationships=[
                rel(kind="runs_on", target_id="host:demo-vc01:esx01"),
                rel(kind="uses_network", target_id="network:demo-vc01:seg-app"),
                rel(kind="uses_datastore", target_id="datastore:demo-vc01:vsanDatastore"),
            ],
        ),
        Resource(
            id="vm:demo-vc01:db01",
            type="vm",
            name="db01",
            source=src,
            parent_id="host:demo-vc01:esx01",
            properties={
                "powerState": "poweredOn",
                "cpu": 8,
                "memoryMB": 32768,
                "toolsStatus": "toolsOk",
            },
            relationships=[
                rel(kind="runs_on", target_id="host:demo-vc01:esx01"),
                rel(kind="uses_network", target_id="network:demo-vc01:seg-app"),
                rel(kind="uses_datastore", target_id="datastore:demo-vc01:vsanDatastore"),
            ],
        ),
        Resource(
            id="vm:demo-vc01:web01",
            type="vm",
            name="web01",
            source=src,
            parent_id="host:demo-vc01:esx02",
            properties={
                "powerState": vm3_power,
                "cpu": 2,
                "memoryMB": 4096,
                "toolsStatus": "toolsOk",
            },
            relationships=[
                rel(kind="runs_on", target_id="host:demo-vc01:esx02"),
                rel(kind="uses_network", target_id="network:demo-vc01:seg-app"),
                rel(kind="uses_datastore", target_id="datastore:demo-vc01:vsanDatastore"),
            ],
        ),
    ]


def parse_offset(value: str | int | float) -> timedelta:
    """ "-00:07:30" -> 7 min 30 s before scan time. Plain numbers are seconds."""
    if isinstance(value, int | float):
        return timedelta(seconds=float(value))
    m = _OFFSET_RE.match(str(value).strip())
    if not m:
        raise ValueError(f"bad event offset {value!r}, expected [-]HH:MM:SS")
    sign = -1 if m.group(1) == "-" else 1
    h, mnt, sec = (int(m.group(i)) for i in (2, 3, 4))
    return sign * timedelta(hours=h, minutes=mnt, seconds=sec)


def load_fixture_events(
    connection_id: str, at: datetime, name: str = EVENTS_FIXTURE
) -> list[Event]:
    """Materialize fixtures/events_b.json: offsets become absolute times
    relative to `at`, fixture resource ids are re-keyed onto the connection."""
    base = fixtures_dir()
    if base is None or not (base / name).is_file():
        return []
    with (base / name).open() as fh:
        payload = json.load(fh)
    items = payload.get("events", payload) if isinstance(payload, dict) else payload
    out: list[Event] = []
    for item in items:
        rid = item.get("resource_id")
        if rid:
            parts = rid.split(":", 2)
            if len(parts) == 3:
                rid = f"{parts[0]}:{connection_id}:{parts[2]}"
        out.append(
            Event(
                id=f"{connection_id}:{item['key']}",
                connection_id=connection_id,
                time=at + parse_offset(item.get("offset", 0)),
                source=item.get("source", "event"),
                type=item["type"],
                category=item.get("category", "info"),
                message=item.get("message", ""),
                user=item.get("user"),
                resource_id=rid,
                resource_name=item.get("resource_name"),
                resource_type=item.get("resource_type"),
            )
        )
    return out


class FixtureCollector(Collector):
    id = "fixture"
    resource_types = ["vcenter", "datacenter", "cluster", "host", "vm", "datastore", "network"]

    def __init__(self, connection_id: str, sequence: int = 0):
        """sequence is how many snapshots already exist for the connection."""
        self.connection_id = connection_id
        self.sequence = sequence

    def test_connection(self) -> ConnectionResult:
        where = fixtures_dir()
        return ConnectionResult(
            ok=True,
            message=f"Fixture data ({where or 'built-in'})",
            version="8.0.3",
            build="fixture",
        )

    def collect(self) -> list[Resource]:
        degraded = self.sequence > 0
        name = "snapshot_b.json" if degraded else "snapshot_a.json"
        loaded = load_fixture(name)
        if loaded is None and degraded:
            loaded = load_fixture("snapshot_a.json")
        resources = loaded if loaded is not None else builtin_resources(degraded)
        return namespace_resources(resources, self.connection_id)

    def collect_events(self, since: datetime, until: datetime) -> list[Event]:
        """Nothing on the first scan (the healthy baseline). From the second
        scan on, the A -> B story from fixtures/events_b.json, timed relative
        to the scan. Same ids every time, so the store keeps one copy."""
        if self.sequence < 1:
            return []
        at = until if until.tzinfo else until.replace(tzinfo=UTC)
        return load_fixture_events(self.connection_id, at)


def namespace_resources(resources: list[Resource], namespace: str) -> list[Resource]:
    """Rewrite the fixture's vCenter key with the connection id so two fixture
    connections never share resource ids."""
    if not resources:
        return resources
    key = resources[0].source.split(":", 1)[-1]
    if not key or key == namespace:
        return resources
    old, new = f":{key}", f":{namespace}"

    def fix(value: str | None) -> str | None:
        return value.replace(old, new, 1) if value else value

    out: list[Resource] = []
    for r in resources:
        out.append(
            r.model_copy(
                update={
                    "id": fix(r.id),
                    "source": fix(r.source),
                    "parent_id": fix(r.parent_id),
                    "relationships": [
                        rel.model_copy(update={"target_id": fix(rel.target_id)})
                        for rel in r.relationships
                    ],
                }
            )
        )
    return out
