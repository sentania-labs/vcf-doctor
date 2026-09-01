"""Semantic diff between two snapshots' resource lists.

Not a raw JSON diff: only the properties that matter operationally are
compared, and each change gets a significance from a fixed per-type table
(docs/PROPERTIES.md). A Change carries the highest significance among its
property changes; property_changes keeps {old, new} per property (for lists,
old and new are the whole lists).

The engine must never crash on an unexpected shape: values that cannot be
hashed or sorted are coerced with str() or compared by a JSON rendering.
"""

import json
import logging
from typing import Any

from app.diagnostics.checks._common import cluster_members, datastore_usage_pct, host_cluster_id
from app.models import Change, Resource, Significance
from app.models.change import PropertyChange

log = logging.getLogger(__name__)

# Per-type property -> significance. `name` (medium) applies to every type and
# is handled separately so datacenters and other untabled types get it too.
SIGNIFICANCE: dict[str, dict[str, Significance]] = {
    "host": {
        "connectionState": "high",
        "powerState": "high",
        "vmkernelAdapters": "high",
        "maintenanceMode": "medium",
        "cluster": "medium",
        "version": "medium",
        "build": "medium",
        "lockdownMode": "medium",
        "ntpServers": "medium",
        "dnsServers": "medium",
        "bootTime": "medium",
        "model": "low",
        "memoryBytes": "low",
        "numCpuCores": "low",
        "physicalNics": "low",
        "standardSwitches": "low",
    },
    "vm": {
        "powerState": "medium",
        "networks": "medium",
        "datastores": "medium",
        "disks": "medium",
        "nics": "medium",
        "host": "low",
        "numCpu": "low",
        "memoryMB": "low",
        "hardwareVersion": "low",
        "template": "low",
        "snapshotCount": "low",
        "resourcePool": "low",
        "folder": "low",
        "cpuReservationMhz": "low",
        "memReservationMB": "low",
        "toolsStatus": "low",
        "guestIp": "low",
        "annotation": "low",
        "bootTime": "low",
    },
    "cluster": {
        "drsEnabled": "high",
        "haEnabled": "high",
        "vsanEnabled": "high",
        "hosts": "medium",
        "drsAutomationLevel": "medium",
        "haAdmissionControl": "medium",
        "evcMode": "medium",
        "ruleCount": "low",
    },
    "datastore": {
        "accessible": "high",
        "capacity": "medium",
        "freeSpace": "medium",  # banded: only reported on a real move, high past 95%
        "hosts": "medium",
        "maintenanceMode": "medium",
        "multipleHostAccess": "low",
    },
    "network": {
        "vlan": "high",
        "switch": "medium",
        "numPorts": "low",
        "hosts": "low",
    },
    "vcenter": {
        "version": "medium",
        "build": "medium",
        "apiVersion": "low",
    },
}
NAME_SIGNIFICANCE: Significance = "medium"

# List-valued properties compared as unordered collections of scalars.
SCALAR_LISTS = frozenset(
    {"ntpServers", "dnsServers", "networks", "datastores", "hosts", "standardSwitches"}
)
# List-of-dict properties compared item by item, keyed by the named field.
DICT_LISTS: dict[str, str] = {
    "vmkernelAdapters": "device",
    "physicalNics": "device",
    "disks": "label",
    "nics": "label",
}
# Properties whose values may be namespaced ids; summaries show the short name.
_ID_VALUED = frozenset({"host", "cluster", "hosts", "networks", "datastores"})
# Properties only compared when both sides carry a value (a powered-off VM
# has no bootTime; that is not a reboot).
_BOTH_SIDES_REQUIRED = frozenset({"bootTime"})

USAGE_BANDS = (85.0, 95.0)
USAGE_MIN_DELTA = 5.0

_SIG_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


# ---------------------------------------------------------------- helpers


def _json_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set | frozenset):
        return list(value)
    return [value]


def _norm(value: Any) -> Any:
    """Order-insensitive comparison for list-valued properties. Falls back to
    a JSON rendering when the items are not mutually sortable."""
    if isinstance(value, list | tuple | set | frozenset):
        items = list(value)
        try:
            return sorted(items)
        except TypeError:
            return sorted(items, key=_json_key)
    return value


def _fmt(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_fmt(v) for v in value) + "]" if value else "[]"
    if isinstance(value, dict):
        return _json_key(value)
    return str(value)


def _short(value: Any) -> str:
    """Last segment of a namespaced id, e.g. host:vc01:esx02 -> esx02."""
    if isinstance(value, str) and ":" in value:
        return value.rsplit(":", 1)[-1]
    return _fmt(value)


def _item_str(value: Any) -> str:
    """Hashable, deterministic rendering of a list item of any shape."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list | tuple):
        return _json_key(value)
    return _fmt(value)


def _band(pct: float) -> int:
    return sum(1 for b in USAGE_BANDS if pct >= b)


def _free_space_significant(old: Resource, new: Resource) -> bool:
    old_pct = datastore_usage_pct(old)
    new_pct = datastore_usage_pct(new)
    if old_pct is None or new_pct is None:
        return old_pct != new_pct
    if abs(new_pct - old_pct) >= USAGE_MIN_DELTA:
        return True
    return _band(old_pct) != _band(new_pct)


def _value(r: Resource, prop: str, members: dict[str, set[str]]) -> Any:
    if prop == "name":
        return r.name
    if r.type == "host" and prop == "cluster":
        return host_cluster_id(r)
    if r.type == "cluster" and prop == "hosts":
        # Membership is derived from the cluster's hosts property and from
        # the hosts themselves, so either side of the relationship counts.
        return sorted(members.get(r.id, set()))
    return r.properties.get(prop)


def _max_sig(sigs: list[Significance]) -> Significance:
    return min(sigs, key=lambda s: _SIG_RANK[s]) if sigs else "low"


# ---------------------------------------------------------------- significance


def _added_significance(r: Resource) -> Significance:
    return "low"


def _removed_significance(r: Resource) -> Significance:
    if r.type in ("host", "network", "datastore"):
        return "high"
    if r.type == "vm":
        return "medium"
    return "low"


def _significance(rtype: str, prop: str) -> Significance:
    if prop == "name":
        return NAME_SIGNIFICANCE
    return SIGNIFICANCE.get(rtype, {}).get(prop, "low")


def _tracked_props(rtype: str) -> list[str]:
    return ["name", *SIGNIFICANCE.get(rtype, {})]


# ---------------------------------------------------------------- summaries


def _scalar_summary(prop: str, old: Any, new: Any) -> str:
    if prop == "name":
        return f"renamed {_fmt(old)} -> {_fmt(new)}"
    if prop == "maintenanceMode":
        return "entered maintenance mode" if new else "exited maintenance mode"
    if prop == "accessible":
        return "became inaccessible" if new is False else "became accessible"
    if prop == "bootTime":
        return f"rebooted {_fmt(old)} -> {_fmt(new)}"
    if prop in _ID_VALUED:
        return f"{prop} {_short(old)} -> {_short(new)}"
    return f"{prop} {_fmt(old)} -> {_fmt(new)}"


def _scalar_list_summary(prop: str, old: Any, new: Any) -> str:
    before = {_item_str(x) for x in _as_list(old)}
    after = {_item_str(x) for x in _as_list(new)}
    show = _short if prop in _ID_VALUED else str
    parts: list[str] = []
    added = sorted(after - before)
    removed = sorted(before - after)
    if added:
        parts.append("added " + ", ".join(show(x) for x in added))
    if removed:
        parts.append("removed " + ", ".join(show(x) for x in removed))
    return f"{prop} " + "; ".join(parts) if parts else f"{prop} changed"


def _keyed(items: Any, key: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in _as_list(items):
        if isinstance(item, dict):
            k = item.get(key)
            label = str(k) if k is not None else _json_key(item)
        else:
            label = _item_str(item)
        out[label] = item
    return out


def _dict_list_summary(prop: str, key: str, old: Any, new: Any) -> str:
    before = _keyed(old, key)
    after = _keyed(new, key)
    parts: list[str] = []
    for label in sorted(set(after) - set(before)):
        parts.append(f"{label} added")
    for label in sorted(set(before) - set(after)):
        parts.append(f"{label} removed")
    for label in sorted(set(before) & set(after)):
        a, b = before[label], after[label]
        if not (isinstance(a, dict) and isinstance(b, dict)):
            if _norm(a) != _norm(b):
                parts.append(f"{label} changed")
            continue
        for field in sorted(set(a) | set(b)):
            if field == key:
                continue
            if _norm(a.get(field)) != _norm(b.get(field)):
                parts.append(f"{label} {field} {_fmt(a.get(field))} -> {_fmt(b.get(field))}")
    return "; ".join(parts) or f"{prop} changed"


def _prop_summary(prop: str, old: Any, new: Any) -> str:
    if prop in DICT_LISTS:
        return _dict_list_summary(prop, DICT_LISTS[prop], old, new)
    if prop in SCALAR_LISTS or isinstance(old, list) or isinstance(new, list):
        return _scalar_list_summary(prop, old, new)
    return _scalar_summary(prop, old, new)


# ---------------------------------------------------------------- engine


def _diff_free_space(
    old: Resource,
    new: Resource,
    props: dict[str, PropertyChange],
    sigs: list[Significance],
    parts: list[str],
) -> None:
    if not _free_space_significant(old, new):
        return
    o_pct, n_pct = datastore_usage_pct(old), datastore_usage_pct(new)
    props["freeSpace"] = PropertyChange(
        old=old.properties.get("freeSpace"), new=new.properties.get("freeSpace")
    )
    props["usagePct"] = PropertyChange(old=o_pct, new=n_pct)
    sigs.append("high" if (n_pct or 0) >= USAGE_BANDS[1] else "medium")
    parts.append(f"usage {_fmt(o_pct)}% -> {_fmt(n_pct)}%")


def _diff_modified(
    old: Resource,
    new: Resource,
    old_members: dict[str, set[str]],
    new_members: dict[str, set[str]],
) -> Change | None:
    props: dict[str, PropertyChange] = {}
    sigs: list[Significance] = []
    parts: list[str] = []
    for prop in _tracked_props(new.type):
        try:
            o = _value(old, prop, old_members)
            n = _value(new, prop, new_members)
            if _norm(o) == _norm(n):
                continue
            if prop in _BOTH_SIDES_REQUIRED and (o is None or n is None):
                continue
            if new.type == "datastore" and prop == "freeSpace":
                _diff_free_space(old, new, props, sigs, parts)
                continue
            props[prop] = PropertyChange(old=o, new=n)
            sigs.append(_significance(new.type, prop))
            parts.append(_prop_summary(prop, o, n))
        except Exception:  # noqa: BLE001 - one odd property must not sink the diff
            log.exception("diff of %s.%s on %s failed; skipping", new.type, prop, new.id)
    if not props:
        return None
    return Change(
        change_type="modified",
        resource_id=new.id,
        resource_type=new.type,
        resource_name=new.name,
        property_changes=props,
        significance=_max_sig(sigs),
        summary="; ".join(parts),
    )


def diff(
    old: list[Resource], new: list[Resource], min_significance: Significance = "low"
) -> list[Change]:
    """Compare two resource lists by id. Returns changes at or above
    min_significance, sorted high significance first, then by resource_type,
    then resource name."""
    if min_significance not in _SIG_RANK:
        raise ValueError(
            f"min_significance must be one of low, medium, high; got {min_significance!r}"
        )
    old_by_id = {r.id: r for r in old}
    new_by_id = {r.id: r for r in new}
    old_members = cluster_members(old)
    new_members = cluster_members(new)
    changes: list[Change] = []

    for rid, r in new_by_id.items():
        if rid not in old_by_id:
            changes.append(
                Change(
                    change_type="added",
                    resource_id=rid,
                    resource_type=r.type,
                    resource_name=r.name,
                    significance=_added_significance(r),
                    summary=f"{r.type} {r.name} added",
                )
            )
    for rid, r in old_by_id.items():
        if rid not in new_by_id:
            changes.append(
                Change(
                    change_type="removed",
                    resource_id=rid,
                    resource_type=r.type,
                    resource_name=r.name,
                    significance=_removed_significance(r),
                    summary=f"{r.type} {r.name} removed",
                )
            )
    for rid, r in new_by_id.items():
        prev = old_by_id.get(rid)
        if prev is None:
            continue
        change = _diff_modified(prev, r, old_members, new_members)
        if change is not None:
            changes.append(change)

    threshold = _SIG_RANK[min_significance]
    changes = [c for c in changes if _SIG_RANK[c.significance] <= threshold]
    changes.sort(
        key=lambda c: (_SIG_RANK[c.significance], c.resource_type, c.resource_name, c.resource_id)
    )
    return changes


__all__ = ["DICT_LISTS", "SCALAR_LISTS", "SIGNIFICANCE", "diff"]
