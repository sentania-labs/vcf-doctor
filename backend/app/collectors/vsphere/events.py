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
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any

from app.models.event import Event

log = logging.getLogger(__name__)

PAGE_SIZE = 1000
MAX_ITEMS = 20_000  # safety cap per window per kind

# Managed object types a vCenter can reference from event or task history
# that the installed pyVmomi does not define. vCenter 9.1 returns
# ContentLibrary entities and pyVmomi 9.1.0.0 has no such type, so one
# reference failed the whole page and with it the whole capture for that
# connection (issue #65). These are registered as placeholder types before
# the first fetch; any other candidate type met at read time is tried as a
# placeholder by _drain, which preserves the reported name if the candidate
# proves to be a data object type instead.
KNOWN_MISSING_TYPES: tuple[str, ...] = ("ContentLibrary",)
MAX_PLACEHOLDER_TYPES = 8  # distinct registrations per drain before giving up
_TYPE_NAME = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
_placeholder_lock = threading.Lock()
_placeholders: set[str] = set()
_wrong_placeholders: set[str] = set()

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


# ---- unknown managed object types -------------------------------------------------


def pyvmomi_version() -> str:
    try:
        return package_version("pyvmomi")
    except PackageNotFoundError:  # pragma: no cover  (always installed with the app)
        return "unknown"


def unknown_type_name(exc: BaseException) -> str | None:
    """The candidate type a pyVmomi KeyError names, or None.

    While deserializing a response pyVmomi raises KeyError(name) from
    GuessWsdlType, or KeyError("<namespace> <name>") from GetWsdlType, when
    the response references a type it cannot load. Any other KeyError is
    somebody else's bug and is left alone.
    """
    if not isinstance(exc, KeyError) or not exc.args or not isinstance(exc.args[0], str):
        return None
    name = exc.args[0].strip().rsplit(" ", 1)[-1]
    return name if _TYPE_NAME.match(name) else None


def register_placeholder_type(name: str) -> bool:
    """Teach pyVmomi a managed object type it does not define.

    The placeholder derives from vim.ManagedEntity because that is what event
    arguments and TaskInfo.entity are declared as; a plain ManagedObject would
    fail pyVmomi's field type check. The deserialized reference carries the
    _wsdlName and _moId that map_event and map_task already read, so the row
    is kept with resource_type set to the lower-cased type name. Returns True
    when the type is served by a placeholder (registered now or earlier) and
    False when pyVmomi defines it natively.
    """
    from pyVmomi import VmomiSupport

    with _placeholder_lock:
        if name in _placeholders:
            return True
        try:
            VmomiSupport.GuessWsdlType(name)
            return False
        except KeyError:
            pass
        VmomiSupport.CreateManagedType(
            f"vim.{name}", name, "vim.ManagedEntity", "vim.version.version1", [], []
        )
        _placeholders.add(name)
        log.info(
            "registered placeholder managed object type %r; pyVmomi %s does not define it",
            name,
            pyvmomi_version(),
        )
        return True


def placeholder_types() -> frozenset[str]:
    """Types currently served by a placeholder (for tests and diagnostics)."""
    return frozenset(_placeholders)


def ensure_known_types() -> None:
    """Register every KNOWN_MISSING_TYPES entry pyVmomi still lacks."""
    for name in KNOWN_MISSING_TYPES:
        register_placeholder_type(name)


# ---- live fetch ------------------------------------------------------------------


@dataclass(frozen=True)
class FetchBatch:
    items: list[Any]
    complete: bool


@dataclass(frozen=True)
class CaptureBatch:
    events: list[Event]
    complete: bool
    task_history_unavailable: bool | None = None
    error: str | None = None


def _read_pages(collector: Any, reader: str) -> FetchBatch:
    """Rewind a history collector and page through it up to MAX_ITEMS."""
    out: list[Any] = []
    complete = False
    collector.RewindCollector()
    while len(out) < MAX_ITEMS:
        page = list(getattr(collector, reader)(min(PAGE_SIZE, MAX_ITEMS - len(out))) or [])
        if not page:
            complete = True
            break
        out.extend(page)
    return FetchBatch(items=out[:MAX_ITEMS], complete=complete and len(out) < MAX_ITEMS)


def _drain(collector: Any, reader: str) -> FetchBatch:
    """Read a whole history collector, surviving managed object types pyVmomi
    does not know.

    A vCenter newer than the installed pyVmomi can hand back a reference to a
    type pyVmomi cannot deserialize. pyVmomi raises KeyError naming the type
    and the whole page is lost with it, which used to fail the capture for the
    connection. Register a placeholder for that type, log which read expected
    it, rewind and read the window again. Bounded: the same name twice, more
    than MAX_PLACEHOLDER_TYPES names, a KeyError that names no type, or a type
    pyVmomi already defines all re-raise the original error. A KeyError that
    names no type after a registration means the latest placeholder was wrong
    (the name was a data object type, not a managed one), so the latest error,
    the one naming the real type, is what surfaces. On a later drain no
    registration happens, so the cause is a placeholder recorded earlier: with
    one such name it is named as the cause, and with several the error says a
    previously registered unknown class failed the read and lists those names as
    candidates rather than blaming them all.
    """
    registered: list[str] = []
    last_error: KeyError | None = None
    try:
        while True:
            try:
                return _read_pages(collector, reader)
            except KeyError as exc:
                name = unknown_type_name(exc)
                if name is None:
                    if last_error is not None:
                        with _placeholder_lock:
                            _wrong_placeholders.add(registered[-1])
                        raise last_error from exc
                    with _placeholder_lock:
                        wrong_names = sorted(_wrong_placeholders)
                    if len(wrong_names) == 1:
                        raise KeyError(wrong_names[0]) from exc
                    if wrong_names:
                        raise KeyError(
                            "read failed on a previously registered unknown class; "
                            f"possible causes: {', '.join(wrong_names)}"
                        ) from exc
                if (
                    name is None
                    or name in registered
                    or len(registered) >= MAX_PLACEHOLDER_TYPES
                    or not register_placeholder_type(name)
                ):
                    raise
                last_error = exc
                registered.append(name)
                log.warning(
                    "%s returned managed object type %r that pyVmomi %s does not define; "
                    "registered a placeholder and re-reading the window",
                    reader,
                    name,
                    pyvmomi_version(),
                )
    finally:
        try:
            collector.DestroyCollector()
        except Exception:  # noqa: BLE001  best effort teardown
            pass


def fetch_events(si: Any, begin: datetime, end: datetime) -> FetchBatch:
    from pyVmomi import vim

    content = si.RetrieveContent()
    spec = vim.event.EventFilterSpec(
        time=vim.event.EventFilterSpec.ByTime(beginTime=begin, endTime=end)
    )
    collector = content.eventManager.CreateCollectorForEvents(spec)
    return _drain(collector, "ReadNextEvents")


def fetch_tasks(si: Any, begin: datetime, end: datetime) -> FetchBatch:
    from pyVmomi import vim

    content = si.RetrieveContent()
    spec = vim.TaskFilterSpec(
        time=vim.TaskFilterSpec.ByTime(timeType="startedTime", beginTime=begin, endTime=end)
    )
    collector = content.taskManager.CreateCollectorForTasks(spec)
    return _drain(collector, "ReadNextTasks")


def collect_events(si: Any, namespace: str, begin: datetime, end: datetime) -> CaptureBatch:
    """Events plus tasks for the window, with explicit cap completeness."""
    from pyVmomi import vim, vmodl

    ensure_known_types()
    out: list[Event] = []
    event_batch = fetch_events(si, begin, end)
    for raw in event_batch.items:
        try:
            out.append(map_event(raw, namespace))
        except Exception as exc:  # noqa: BLE001  one odd event must not drop the batch
            log.debug("skipping unmappable event %r: %s", getattr(raw, "key", "?"), exc)
    try:
        task_batch = fetch_tasks(si, begin, end)
    except (vmodl.fault.NotSupported, vim.fault.NoPermission) as exc:
        log.warning("task history unavailable, events only: %s", exc)
        return CaptureBatch(
            events=out, complete=event_batch.complete, task_history_unavailable=True
        )
    except Exception as exc:
        log.warning("task history fetch failed: %s", exc)
        return CaptureBatch(events=out, complete=False, error="task history fetch failed")
    for raw in task_batch.items:
        try:
            out.append(map_task(raw, namespace))
        except Exception as exc:  # noqa: BLE001
            log.debug("skipping unmappable task %r: %s", getattr(raw, "key", "?"), exc)
    return CaptureBatch(
        events=out,
        complete=event_batch.complete and task_batch.complete,
        task_history_unavailable=False,
    )
