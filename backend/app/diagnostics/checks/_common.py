"""Shared helpers for diagnostic checks. Pure functions over Resource data."""

from typing import Any

from app.models import Finding, Resource, Severity


def finding_id(check_id: str, resource_id: str) -> str:
    """Deterministic finding id: the same problem gets the same id on every scan."""
    return f"{check_id}:{resource_id}"


def make_finding(
    check_id: str,
    resource: Resource,
    severity: Severity,
    title: str,
    summary: str,
    evidence: dict[str, Any],
    recommendation: str,
) -> Finding:
    return Finding(
        id=finding_id(check_id, resource.id),
        check_id=check_id,
        severity=severity,
        title=title,
        summary=summary,
        resource_id=resource.id,
        resource_name=resource.name,
        resource_type=resource.type,
        evidence=evidence,
        recommendation=recommendation,
    )


def by_type(resources: list[Resource], rtype: str) -> list[Resource]:
    return [r for r in resources if r.type == rtype]


def by_id(resources: list[Resource]) -> dict[str, Resource]:
    return {r.id: r for r in resources}


def host_cluster_id(
    host: Resource,
    cluster_ids: set[str] | None = None,
    cluster_names: dict[str, str] | None = None,
) -> str | None:
    """Cluster id a host belongs to.

    Prefers structural evidence (a member_of relationship, then parent_id when
    it points at a known cluster). Falls back to properties.cluster, which
    collectors populate with the cluster NAME, resolved through cluster_names
    (name -> id) when supplied. A bare name with no resolution is returned
    as-is so callers without a cluster list still get something stable."""
    for rel in host.relationships:
        if rel.kind == "member_of":
            return rel.target_id
    if host.parent_id and (cluster_ids is None or host.parent_id in cluster_ids):
        return host.parent_id
    cluster = host.properties.get("cluster")
    if isinstance(cluster, str) and cluster:
        if cluster_ids and cluster in cluster_ids:
            return cluster
        if cluster_names and cluster in cluster_names:
            return cluster_names[cluster]
        return cluster
    return None


def cluster_members(resources: list[Resource]) -> dict[str, set[str]]:
    """Map cluster id -> set of host ids. Uses an explicit hosts property on
    the cluster when present, otherwise derives it from the hosts."""
    clusters = by_type(resources, "cluster")
    cluster_ids = {c.id for c in clusters}
    cluster_names = {c.name: c.id for c in clusters}
    members: dict[str, set[str]] = {c.id: set() for c in clusters}
    for c in clusters:
        hosts = c.properties.get("hosts")
        if isinstance(hosts, list):
            members[c.id].update(str(h) for h in hosts)
    for h in by_type(resources, "host"):
        cid = host_cluster_id(h, cluster_ids, cluster_names)
        if cid is not None:
            members.setdefault(cid, set()).add(h.id)
    return members


def datastore_usage_pct(ds: Resource) -> float | None:
    """Used percentage rounded to one decimal, or None when capacity is unknown."""
    capacity = ds.properties.get("capacity")
    free = ds.properties.get("freeSpace")
    if not isinstance(capacity, int | float) or isinstance(capacity, bool) or capacity <= 0:
        return None
    if not isinstance(free, int | float) or isinstance(free, bool):
        return None
    used = max(0.0, min(1.0, 1.0 - (float(free) / float(capacity))))
    return round(used * 100.0, 1)


def is_template(vm: Resource) -> bool:
    p = vm.properties
    return bool(p.get("template") or p.get("isTemplate"))
