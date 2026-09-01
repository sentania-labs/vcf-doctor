"""vCenter events and tasks: fetch for a time window and map to `Event`.

Fetching (`fetch_events`, `fetch_tasks`) is the only part that needs a live
ServiceInstance. Mapping (`map_event`, `map_task`, `classify_event`) works on
plain attribute access so it is unit-tested with SimpleNamespace fakes.

Mapping rules
    id            "<namespace>:<event key>"  /  "<namespace>:task:<task key>"
    time          createdTime (events), completeTime|startTime|queueTime (tasks)
    type          the vim class name, e.g. VmPoweredOffEvent; for EventEx and
                  ExtendedEvent the eventTypeId; for tasks the descriptionId
    message       fullFormattedMessage (events); "<description> on <entity>:
                  <state>[: <error>]" for tasks
    user          userName (events), reason.userName for user-started tasks
    resource_*    first present entity argument in the order vm, host, ds, net,
                  dvs, computeResource, datacenter, entity; the moref class
                  gives the type and "<type>:<namespace>:<moref>" the id, the
                  same scheme normalize.py uses for snapshot resources
    category      error    class name carries Error/Failed/Fault/ConnectionLost,
                           severity == "error", alarm went red, task failed
                  warning  severity == "warning", class name carries Warning
                           or Alarm (alarm not red)
                  user     userName is a person (not empty, not vpxuser, not
                           vpxd-extension*, not a com.vmware.* service account)
                  info     everything else
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.models.event import Event

log = logging.getLogger(__name__)

PAGE_SIZE = 1000
MAX_ITEMS = 20_000  # safety cap per window per kind

# (attribute on the event, attribute on the EventArgument holding the moref)
ENTITY_ARGS: tuple[tuple[str, str], ...] = (
    ("vm", "vm"),
    ("host", "host"),
    ("ds", "datastore"),
    ("net", "network"),
    ("dvs", "dvs"),
    ("computeResource", "computeResource"),
    ("datacenter", "datacenter"),
    ("entity", "entity"),  # AlarmEvent and friends: ManagedEntityEventArgument
)

# wsdl name of the moref class -> resource type in the snapshot id scheme
MOREF_TYPES: dict[str, str] = {
    "VirtualMachine": "vm",
    "HostSystem": "host",
    "Datastore": "datastore",
    "Network": "network",
    "DistributedVirtualPortgroup": "network",
    "OpaqueNetwork": "network",
    "ClusterComputeResource": "cluster",
    "ComputeResource": "cluster",
    "Datacenter": "datacenter",
    "DistributedVirtualSwitch": "dvs",
    "VmwareDistributedVirtualSwitch": "dvs",
}

SYSTEM_USERS = {"", "vpxuser", "vpxd-extension", "system", "root"}
SYSTEM_USER_PREFIXES = ("vpxd-extension", "vpxuser", "com.vmware.", "vsphere-webclient", "vpxd-")
ERROR_MARKERS = ("Error", "Failed", "Fault", "ConnectionLost")
WARNING_MARKERS = ("Warning", "Alarm")


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _moref_parts(ref: Any) -> tuple[str | None, str | None]:
    """(wsdl type name, moId) for a managed object reference, or (None, None)."""
    if ref is None:
        return None, None
    moid = getattr(ref, "_moId", None)
    if moid is None:
        return None, None
    kind = getattr(ref, "_wsdlName", None) or type(ref).__name__
    return str(kind), str(moid)


def is_system_user(user: str | None) -> bool:
    if user is None:
        return True
    u = user.strip().lower()
    # Strip a DOMAIN\ prefix; keep the @domain suffix for the prefix checks.
    if "\\" in u:
        u = u.rsplit("\\", 1)[1]
    if u in SYSTEM_USERS:
        return True
    return any(u.startswith(p) for p in SYSTEM_USER_PREFIXES)


def resolve_entity(event: Any, namespace: str) -> tuple[str | None, str | None, str | None]:
    """(resource_id, resource_name, resource_type) from the first entity argument."""
    for attr, ref_attr in ENTITY_ARGS:
        arg = getattr(event, attr, None)
        if arg is None:
            continue
        kind, moid = _moref_parts(getattr(arg, ref_attr, None))
        name = _text(getattr(arg, "name", None))
        if kind is None:
            if name:
                return None, name, None
            continue
        rtype = MOREF_TYPES.get(kind, kind.lower())
        return f"{rtype}:{namespace}:{moid}", name, rtype
    return None, None, None


def classify_event(event: Any, type_name: str, user: str | None) -> str:
    severity = (_text(getattr(event, "severity", None)) or "").lower()
    if severity == "error" or any(m in type_name for m in ERROR_MARKERS):
        return "error"
    if type_name.startswith("Alarm"):
        to = (_text(getattr(event, "to", None)) or "").lower()
        if to == "red":
            return "error"
        return "warning"
    if severity == "warning" or any(m in type_name for m in WARNING_MARKERS):
        return "warning"
    if not is_system_user(user):
        return "user"
    return "info"


def event_type_name(event: Any) -> str:
    name = type(event).__name__
    if name in ("EventEx", "ExtendedEvent"):
        return _text(getattr(event, "eventTypeId", None)) or name
    return name


def map_event(event: Any, namespace: str) -> Event:
    type_name = event_type_name(event)
    user = _text(getattr(event, "userName", None))
    rid, rname, rtype = resolve_entity(event, namespace)
    return Event(
        id=f"{namespace}:{event.key}",
        connection_id=namespace,
        time=_utc(getattr(event, "createdTime", None)),
        source="event",
        type=type_name,
        category=classify_event(event, type_name, user),
        message=_text(getattr(event, "fullFormattedMessage", None)) or type_name,
        user=user,
        resource_id=rid,
        resource_name=rname,
        resource_type=rtype,
    )


def _task_user(task: Any) -> str | None:
    reason = getattr(task, "reason", None)
    return _text(getattr(reason, "userName", None)) if reason is not None else None


def _task_error(task: Any) -> str | None:
    err = getattr(task, "error", None)
    if err is None:
        return None
    return _text(getattr(err, "localizedMessage", None) or getattr(err, "msg", None)) or str(err)


def map_task(task: Any, namespace: str) -> Event:
    """TaskInfo -> Event. The entity is a plain moref (no EventArgument wrapper)."""
    kind, moid = _moref_parts(getattr(task, "entity", None))
    rtype = MOREF_TYPES.get(kind, kind.lower()) if kind else None
    rid = f"{rtype}:{namespace}:{moid}" if rtype else None
    type_name = _text(getattr(task, "descriptionId", None)) or "Task"
    desc = getattr(task, "description", None)
    label = _text(getattr(desc, "message", None)) or type_name
    entity_name = _text(getattr(task, "entityName", None))
    state = _text(getattr(task, "state", None)) or "unknown"
    user = _task_user(task)
    error = _task_error(task)
    message = f"{label} on {entity_name}: {state}" if entity_name else f"{label}: {state}"
    if error:
        message = f"{message}: {error}"
    if state == "error":
        category = "error"
    elif not is_system_user(user):
        category = "user"
    else:
        category = "info"
    when = (
        getattr(task, "completeTime", None)
        or getattr(task, "startTime", None)
        or getattr(task, "queueTime", None)
    )
    return Event(
        id=f"{namespace}:task:{task.key}",
        connection_id=namespace,
        time=_utc(when),
        source="task",
        type=type_name,
        category=category,
        message=message,
        user=user,
        resource_id=rid,
        resource_name=entity_name,
        resource_type=rtype,
    )


# ---- live fetch ------------------------------------------------------------------


def _drain(collector: Any, reader: str) -> list[Any]:
    """Rewind a history collector and page through it."""
    out: list[Any] = []
    try:
        collector.RewindCollector()
        while len(out) < MAX_ITEMS:
            page = getattr(collector, reader)(PAGE_SIZE)
            if not page:
                break
            out.extend(page)
    finally:
        try:
            collector.DestroyCollector()
        except Exception:  # noqa: BLE001  best effort teardown
            pass
    return out


def fetch_events(si: Any, begin: datetime, end: datetime) -> list[Any]:
    from pyVmomi import vim

    content = si.RetrieveContent()
    spec = vim.event.EventFilterSpec(
        time=vim.event.EventFilterSpec.ByTime(beginTime=begin, endTime=end)
    )
    collector = content.eventManager.CreateCollectorForEvents(spec)
    return _drain(collector, "ReadNextEvents")


def fetch_tasks(si: Any, begin: datetime, end: datetime) -> list[Any]:
    from pyVmomi import vim

    content = si.RetrieveContent()
    spec = vim.TaskFilterSpec(
        time=vim.TaskFilterSpec.ByTime(timeType="startedTime", beginTime=begin, endTime=end)
    )
    collector = content.taskManager.CreateCollectorForTasks(spec)
    return _drain(collector, "ReadNextTasks")


def collect_events(si: Any, namespace: str, begin: datetime, end: datetime) -> list[Event]:
    """Events plus tasks for the window, mapped. A task history failure (for
    example a standalone host without a task history collector) is logged
    and leaves the events in place."""
    out: list[Event] = []
    for raw in fetch_events(si, begin, end):
        try:
            out.append(map_event(raw, namespace))
        except Exception as exc:  # noqa: BLE001  one odd event must not drop the batch
            log.debug("skipping unmappable event %r: %s", getattr(raw, "key", "?"), exc)
    try:
        tasks = fetch_tasks(si, begin, end)
    except Exception as exc:  # noqa: BLE001
        log.warning("task history unavailable, events only: %s", exc)
        tasks = []
    for raw in tasks:
        try:
            out.append(map_task(raw, namespace))
        except Exception as exc:  # noqa: BLE001
            log.debug("skipping unmappable task %r: %s", getattr(raw, "key", "?"), exc)
    return out
