from typing import Any

from pydantic import BaseModel, Field


class Relationship(BaseModel):
    """Directed edge from the owning resource to target_id."""

    kind: str  # e.g. "runs_on", "uses_network", "uses_datastore", "member_of"
    target_id: str


class Resource(BaseModel):
    """A normalized infrastructure object.

    id must be stable between scans and namespaced by source, e.g.
    "host:vc01:esx03". type is one of: vcenter, datacenter, cluster, host,
    vm, datastore, network (collectors may add more).
    """

    id: str
    type: str
    name: str
    source: str  # e.g. "vcenter:vc01"
    parent_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    relationships: list[Relationship] = Field(default_factory=list)
