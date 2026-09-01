"""Semantic diff between two snapshots' resource lists.

Not a raw JSON diff: only the properties that matter operationally are
compared, and each change gets a significance from a fixed table.
"""

from typing import Any

from app.diagnostics.checks._common import cluster_members, datastore_usage_pct, host_cluster_id
from app.models import Change, Resource, Significance
from app.models.change import PropertyChange

TRACKED: dict[str, tuple[str, ...]] = {
    "host": ("connectionState", "powerState", "maintenanceMode", "cluster"),
    "vm": ("powerState", "host", "networks", "datastores"),
    "datastore": ("accessible", "capacity", "freeSpace"),
}

USAGE_BANDS = (85.0, 95.0)
USAGE_MIN_DELTA = 5.0

_SIG_RANK = {"high": 0, "medium": 1, "low": 2}


def _norm(value: Any) -> Any:
    """Order-insensitive comparison for list-valued properties."""
    if isinstance(value, list):
        try:
            return sorted(value)
        except TypeError:
            return value
    return value


def _fmt(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]" if value else "[]"
    return str(value)


def _short(resource_id: Any) -> str:
    """Last segment of a namespaced id, e.g. host:vc01:esx02 -> esx02."""
    if isinstance(resource_id, str) and ":" in resource_id:
        return resource_id.rsplit(":", 1)[-1]
    return _fmt(resource_id)


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


def _host_cluster(h: Resource) -> Any:
    return host_cluster_id(h)


def _tracked_value(r: Resource, prop: str) -> Any:
    if r.type == "host" and prop == "cluster":
        return _host_cluster(r)
    return r.properties.get(prop)


# ---------------------------------------------------------------- significance


def _added_significance(r: Resource) -> Significance:
    return "low"


def _removed_significance(r: Resource) -> Significance:
    if r.type in ("host", "network", "datastore"):
        return "high"
    if r.type == "vm":
        return "medium"
    return "low"


def _modified_significance(rtype: str, prop: str, old: Any, new: Any) -> Significance:
    if rtype == "host":
        if prop == "connectionState":
            return "high"
        if prop == "maintenanceMode":
            return "medium"
        if prop == "powerState":
            return "high" if new != "poweredOn" else "medium"
        if prop == "cluster":
            return "medium"
    if rtype == "vm":
        if prop == "host":
            return "low"
        if prop == "powerState":
            return "medium"
        if prop in ("networks", "datastores"):
            return "medium"
    if rtype == "datastore":
        if prop == "accessible":
            return "high" if new is False else "medium"
        if prop == "freeSpace":
            return "medium"
        if prop == "capacity":
            return "low"
    if rtype == "cluster" and prop == "hosts":
        return "medium"
    return "low"


def _max_sig(sigs: list[Significance]) -> Significance:
    return min(sigs, key=lambda s: _SIG_RANK[s]) if sigs else "low"


# ---------------------------------------------------------------- summaries


def _prop_summary(rtype: str, prop: str, old: Any, new: Any) -> str:
    if prop in ("host", "cluster") and rtype in ("vm", "host"):
        return f"{_short(old)} -> {_short(new)}"
    if prop == "maintenanceMode":
        return "entered maintenance mode" if new else "exited maintenance mode"
    if prop == "accessible":
        return "became inaccessible" if new is False else "became accessible"
    if prop == "hosts":
        added = sorted(set(new or []) - set(old or []))
        removed = sorted(set(old or []) - set(new or []))
        parts = []
        if added:
            parts.append("added " + ", ".join(_short(h) for h in added))
        if removed:
            parts.append("removed " + ", ".join(_short(h) for h in removed))
        return "; ".join(parts) or "membership changed"
    if prop in ("networks", "datastores"):
        added = sorted(set(new or []) - set(old or []))
        removed = sorted(set(old or []) - set(new or []))
        parts = []
        if added:
            parts.append(f"{prop} added " + ", ".join(_short(x) for x in added))
        if removed:
            parts.append(f"{prop} removed " + ", ".join(_short(x) for x in removed))
        return "; ".join(parts) or f"{prop} changed"
    return f"{_fmt(old)} -> {_fmt(new)}"


# ---------------------------------------------------------------- engine


def _diff_modified(old: Resource, new: Resource) -> Change | None:
    props: dict[str, PropertyChange] = {}
    sigs: list[Significance] = []
    parts: list[str] = []
    for prop in TRACKED.get(new.type, ()):
        o = _tracked_value(old, prop)
        n = _tracked_value(new, prop)
        if _norm(o) == _norm(n):
            continue
        if new.type == "datastore" and prop == "freeSpace":
            if not _free_space_significant(old, new):
                continue
            o_pct, n_pct = datastore_usage_pct(old), datastore_usage_pct(new)
            props[prop] = PropertyChange(old=o, new=n)
            props["usagePct"] = PropertyChange(old=o_pct, new=n_pct)
            sigs.append("high" if (n_pct or 0) >= USAGE_BANDS[1] else "medium")
            parts.append(f"usage {_fmt(o_pct)}% -> {_fmt(n_pct)}%")
            continue
        props[prop] = PropertyChange(old=o, new=n)
        sigs.append(_modified_significance(new.type, prop, o, n))
        parts.append(_prop_summary(new.type, prop, o, n))
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


def _diff_clusters(old: list[Resource], new: list[Resource]) -> list[Change]:
    old_members = cluster_members(old)
    new_members = cluster_members(new)
    new_clusters = {c.id: c for c in new if c.type == "cluster"}
    out: list[Change] = []
    for cid, cluster in new_clusters.items():
        if cid not in old_members:
            continue
        before = sorted(old_members[cid])
        after = sorted(new_members.get(cid, set()))
        if before == after:
            continue
        out.append(
            Change(
                change_type="modified",
                resource_id=cid,
                resource_type="cluster",
                resource_name=cluster.name,
                property_changes={"hosts": PropertyChange(old=before, new=after)},
                significance=_modified_significance("cluster", "hosts", before, after),
                summary=_prop_summary("cluster", "hosts", before, after),
            )
        )
    return out


def diff(old: list[Resource], new: list[Resource]) -> list[Change]:
    """Compare two resource lists by id. Returns changes sorted high
    significance first, then by resource_type, then resource name."""
    old_by_id = {r.id: r for r in old}
    new_by_id = {r.id: r for r in new}
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
        if prev is None or r.type == "cluster":
            continue
        change = _diff_modified(prev, r)
        if change is not None:
            changes.append(change)
    changes.extend(_diff_clusters(old, new))

    changes.sort(
        key=lambda c: (_SIG_RANK[c.significance], c.resource_type, c.resource_name, c.resource_id)
    )
    return changes


__all__ = ["diff"]
