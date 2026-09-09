"""Event capture survives managed object types the installed pyVmomi does not
define (issue #65: KeyError: 'ContentLibrary' from vCenter 9.1).

The failure is in pyVmomi's SOAP deserializer: one reference to an unknown
type raises KeyError(name) and the whole page, then the whole capture for the
connection, is lost. The collector registers a placeholder type and re-reads.
The pyVmomi tests below use made-up type names so they do not depend on what a
future pyVmomi release adds; placeholder registration is process-global.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.collectors.vsphere import events as collector_events
from app.collectors.vsphere.events import (
    KNOWN_MISSING_TYPES,
    _drain,
    ensure_known_types,
    map_event,
    map_task,
    placeholder_types,
    register_placeholder_type,
    unknown_type_name,
)

XMLNS = 'xmlns="urn:vim25" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'


class UnknownTypeCollector:
    """History collector whose page read raises pyVmomi's KeyError until the
    named type has been registered, the way a real read behaves."""

    def __init__(self, rows, missing: list[str], *, registrations: set[str]):
        self.rows = list(rows)
        self.missing = list(missing)
        self.registrations = registrations
        self.rewinds = 0
        self.destroyed = False

    def RewindCollector(self):
        self.rewinds += 1
        self.cursor = 0

    def ReadNextEvents(self, size):
        for name in self.missing:
            if name not in self.registrations:
                raise KeyError(name)
        page = self.rows[self.cursor : self.cursor + size]
        self.cursor += size
        return page

    def DestroyCollector(self):
        self.destroyed = True


@pytest.fixture
def fake_registry(monkeypatch):
    """Stand in for pyVmomi registration so _drain's control flow is tested
    without touching the process-global type maps."""
    registered: set[str] = set()
    native = {"VirtualMachine"}

    def register(name: str) -> bool:
        if name in native:
            return False
        registered.add(name)
        return True

    monkeypatch.setattr(collector_events, "register_placeholder_type", register)
    return registered


def test_drain_registers_placeholder_and_rereads_the_window(fake_registry, caplog):
    collector = UnknownTypeCollector(range(5), ["ContentLibrary"], registrations=fake_registry)
    with caplog.at_level(logging.WARNING, logger=collector_events.__name__):
        result = _drain(collector, "ReadNextEvents")

    assert result.items == [0, 1, 2, 3, 4]
    assert result.complete is True
    assert collector.rewinds == 2
    assert collector.destroyed is True
    assert fake_registry == {"ContentLibrary"}
    [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "ReadNextEvents" in record.getMessage()
    assert "'ContentLibrary'" in record.getMessage()
    assert "pyVmomi" in record.getMessage()


def test_drain_handles_several_unknown_types_in_one_window(fake_registry):
    collector = UnknownTypeCollector(
        range(3), ["ContentLibrary", "SupervisorNamespace"], registrations=fake_registry
    )
    result = _drain(collector, "ReadNextEvents")
    assert result.items == [0, 1, 2]
    assert result.complete is True
    assert collector.rewinds == 3
    assert fake_registry == {"ContentLibrary", "SupervisorNamespace"}


def test_drain_gives_up_when_the_placeholder_does_not_help(monkeypatch):
    monkeypatch.setattr(collector_events, "register_placeholder_type", lambda name: True)

    class Stuck:
        rewinds = 0

        def RewindCollector(self):
            self.rewinds += 1

        def ReadNextEvents(self, size):
            raise KeyError("ContentLibrary")

        def DestroyCollector(self):
            pass

    stuck = Stuck()
    with pytest.raises(KeyError, match="ContentLibrary"):
        _drain(stuck, "ReadNextEvents")
    assert stuck.rewinds == 2


def test_drain_reraises_when_pyvmomi_already_defines_the_type(fake_registry):
    collector = UnknownTypeCollector(range(2), ["VirtualMachine"], registrations=fake_registry)
    with pytest.raises(KeyError, match="VirtualMachine"):
        _drain(collector, "ReadNextEvents")
    assert collector.rewinds == 1
    assert collector.destroyed is True
    assert fake_registry == set()


def test_drain_reraises_keyerrors_that_name_no_type(fake_registry):
    class Other:
        def RewindCollector(self):
            pass

        def ReadNextEvents(self, size):
            raise KeyError(("urn:vim25", "ContentLibrary"))

        def DestroyCollector(self):
            pass

    with pytest.raises(KeyError):
        _drain(Other(), "ReadNextEvents")
    assert fake_registry == set()


def test_drain_bounds_the_number_of_registrations(monkeypatch, fake_registry):
    monkeypatch.setattr(collector_events, "MAX_PLACEHOLDER_TYPES", 2)
    collector = UnknownTypeCollector(range(2), ["A", "B", "C"], registrations=fake_registry)
    with pytest.raises(KeyError, match="C"):
        _drain(collector, "ReadNextEvents")
    assert fake_registry == {"A", "B"}


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (KeyError("ContentLibrary"), "ContentLibrary"),
        (KeyError("urn:vim25 ContentLibrary"), "ContentLibrary"),
        (KeyError(("urn:vim25", "ContentLibrary")), None),
        (KeyError("not a type name!"), None),
        (KeyError("type"), None),
        (KeyError(), None),
        (RuntimeError("ContentLibrary"), None),
    ],
)
def test_unknown_type_name(exc, expected):
    assert unknown_type_name(exc) == expected


# ---- against the real pyVmomi ----------------------------------------------------


def _event_page(type_name: str) -> bytes:
    """A GeneralUserEvent whose entity argument references an unknown type,
    the shape ManagedEntityEventArgument.entity takes on the wire."""
    return f"""<obj {XMLNS} xsi:type="GeneralUserEvent">
<key>101</key><chainId>101</chainId><createdTime>2026-09-09T04:50:00Z</createdTime>
<userName>admin</userName><fullFormattedMessage>Library synced</fullFormattedMessage>
<message>Library synced</message>
<entity><name>Lib One</name><entity type="{type_name}">cl-1</entity></entity>
</obj>""".encode()


def _task_event_page(type_name: str) -> bytes:
    """A TaskEvent whose TaskInfo.entity references an unknown type."""
    return f"""<obj {XMLNS} xsi:type="TaskEvent">
<key>102</key><chainId>102</chainId><createdTime>2026-09-09T04:50:00Z</createdTime>
<userName>admin</userName><fullFormattedMessage>Task: Sync library</fullFormattedMessage>
<info><key>task-9</key><task type="Task">task-9</task>
<descriptionId>com.vmware.cl.SyncLibrary</descriptionId>
<entity type="{type_name}">cl-2</entity><entityName>Lib Two</entityName>
<state>success</state><cancelled>false</cancelled><cancelable>false</cancelable>
<queueTime>2026-09-09T04:49:00Z</queueTime><eventChainId>5</eventChainId></info>
</obj>""".encode()


def test_pyvmomi_fails_the_page_on_an_unknown_entity_type_until_a_placeholder_exists():
    from pyVmomi import SoapAdapter, vim

    name = "VcfDoctorProbeEntityA"
    with pytest.raises(KeyError) as excinfo:
        SoapAdapter.Deserialize(_event_page(name), vim.event.Event)
    assert unknown_type_name(excinfo.value) == name

    assert register_placeholder_type(name) is True
    assert register_placeholder_type(name) is True  # idempotent, still a placeholder
    assert name in placeholder_types()

    raw = SoapAdapter.Deserialize(_event_page(name), vim.event.Event)
    assert isinstance(raw.entity.entity, vim.ManagedEntity)
    mapped = map_event(raw, "conn1")
    assert mapped.resource_type == name.lower()
    assert mapped.resource_id == f"{name.lower()}:conn1:cl-1"
    assert mapped.resource_name == "Lib One"
    assert mapped.category == "user"


def test_pyvmomi_task_entity_of_unknown_type_deserializes_after_placeholder():
    from pyVmomi import SoapAdapter, vim

    name = "VcfDoctorProbeEntityB"
    with pytest.raises(KeyError):
        SoapAdapter.Deserialize(_task_event_page(name), vim.event.Event)
    assert register_placeholder_type(name) is True
    raw = SoapAdapter.Deserialize(_task_event_page(name), vim.event.Event)
    task = map_task(raw.info, "conn1")
    assert task.resource_type == name.lower()
    assert task.resource_id == f"{name.lower()}:conn1:cl-2"
    assert task.resource_name == "Lib Two"


def test_register_placeholder_type_leaves_native_types_alone():
    from pyVmomi import VmomiSupport

    assert register_placeholder_type("VirtualMachine") is False
    assert "VirtualMachine" not in placeholder_types()
    assert VmomiSupport.GuessWsdlType("VirtualMachine").__name__ == "vim.VirtualMachine"


def test_content_library_is_known_missing_and_seeded_before_capture():
    from pyVmomi import VmomiSupport, vim

    assert "ContentLibrary" in KNOWN_MISSING_TYPES
    ensure_known_types()
    ensure_known_types()  # second call is a no-op
    cls = VmomiSupport.GuessWsdlType("ContentLibrary")
    assert issubclass(cls, vim.ManagedEntity)
    assert cls._wsdlName == "ContentLibrary"


def test_collect_events_seeds_known_types_before_fetching(monkeypatch):
    seeded = []
    monkeypatch.setattr(collector_events, "ensure_known_types", lambda: seeded.append(True))
    monkeypatch.setattr(
        collector_events,
        "fetch_events",
        lambda *_a: collector_events.FetchBatch(items=[], complete=True),
    )
    monkeypatch.setattr(
        collector_events,
        "fetch_tasks",
        lambda *_a: collector_events.FetchBatch(items=[], complete=True),
    )
    now = datetime(2026, 9, 9, 4, 50, tzinfo=UTC)
    result = collector_events.collect_events(object(), "conn1", now - timedelta(minutes=5), now)
    assert seeded == [True]
    assert result.complete is True


def test_drain_via_real_pyvmomi_deserializer_recovers(monkeypatch):
    """End to end through _drain: a page read that fails inside pyVmomi on an
    unknown type is retried after registration and returns the mapped rows."""
    from pyVmomi import SoapAdapter, vim

    name = "VcfDoctorProbeEntityC"
    page = _event_page(name)

    class Collector:
        def __init__(self):
            self.rewinds = 0
            self.served = False

        def RewindCollector(self):
            self.rewinds += 1
            self.served = False

        def ReadNextEvents(self, size):
            if self.served:
                return []
            self.served = True
            return [SoapAdapter.Deserialize(page, vim.event.Event)]

        def DestroyCollector(self):
            pass

    collector = Collector()
    batch = _drain(collector, "ReadNextEvents")
    assert batch.complete is True
    assert collector.rewinds == 2
    [raw] = batch.items
    assert raw.entity.entity._wsdlName == name
    assert name in placeholder_types()


def _unknown_event_class_page(class_name: str) -> bytes:
    """An event whose xsi:type is an event class pyVmomi does not define."""
    return f"""<obj {XMLNS} xsi:type="{class_name}">
<key>103</key><chainId>103</chainId><createdTime>2026-09-09T04:50:00Z</createdTime>
<userName>admin</userName><fullFormattedMessage>Probe</fullFormattedMessage>
</obj>""".encode()


def test_drain_reports_the_real_name_for_an_unknown_event_class(monkeypatch):
    """An unknown data object type raises the same KeyError(name) as an
    unknown managed type. The placeholder does not help there, and pyVmomi's
    re-read then fails with KeyError('type'); the error that surfaces must
    still name the event class, and 'type' must never become a placeholder.

    The wrong-placeholder set starts empty here so the later drain exercises
    the single known cause, which is named outright."""
    from pyVmomi import SoapAdapter, vim

    monkeypatch.setattr(collector_events, "_wrong_placeholders", set())

    name = f"VcfProbeNewEvent{uuid4().hex}"
    page = _unknown_event_class_page(name)

    class Collector:
        def __init__(self):
            self.rewinds = 0

        def RewindCollector(self):
            self.rewinds += 1

        def ReadNextEvents(self, size):
            return [SoapAdapter.Deserialize(page, vim.event.Event)]

        def DestroyCollector(self):
            pass

    first = Collector()
    with pytest.raises(KeyError) as first_excinfo:
        _drain(first, "ReadNextEvents")
    assert first_excinfo.value.args == (name,)
    assert first.rewinds == 2

    second = Collector()
    with pytest.raises(KeyError) as second_excinfo:
        _drain(second, "ReadNextEvents")
    assert second_excinfo.value.args == (name,)
    assert second.rewinds == 1
    assert "type" not in placeholder_types()


def test_drain_reports_the_latest_registration_when_its_placeholder_is_wrong(monkeypatch):
    """The wrong-placeholder set is patched as well as the registrar, so this
    drain does not leave a name in the real process-global set and make the
    sibling tests depend on the order they run in."""
    monkeypatch.setattr(collector_events, "register_placeholder_type", lambda name: True)
    monkeypatch.setattr(collector_events, "_wrong_placeholders", set())

    class MixedUnknownTypes:
        rewinds = 0

        def RewindCollector(self):
            self.rewinds += 1

        def ReadNextEvents(self, size):
            if self.rewinds == 1:
                raise KeyError("VcfProbeManagedType")
            if self.rewinds == 2:
                raise KeyError("VcfProbeEventClass")
            raise KeyError("type")

        def DestroyCollector(self):
            pass

    with pytest.raises(KeyError) as excinfo:
        _drain(MixedUnknownTypes(), "ReadNextEvents")
    assert excinfo.value.args == ("VcfProbeEventClass",)


def test_drain_names_several_wrong_placeholders_as_candidates(monkeypatch):
    """A later drain registers nothing, so the cause is a placeholder recorded
    earlier. With more than one recorded, the error must not claim they all
    caused this failure: it says a previously registered unknown class failed
    the read and offers the known names as candidates."""
    monkeypatch.setattr(
        collector_events, "_wrong_placeholders", {"VcfProbeAlphaEvent", "VcfProbeBetaEvent"}
    )

    class LaterScan:
        rewinds = 0

        def RewindCollector(self):
            self.rewinds += 1

        def ReadNextEvents(self, size):
            raise KeyError("type")

        def DestroyCollector(self):
            pass

    later = LaterScan()
    with pytest.raises(KeyError) as excinfo:
        _drain(later, "ReadNextEvents")
    message = excinfo.value.args[0]
    assert "previously registered unknown class" in message
    assert "possible causes" in message
    assert "VcfProbeAlphaEvent" in message
    assert "VcfProbeBetaEvent" in message
    # It must not read as a bare list of names, which claims all of them.
    assert message != "VcfProbeAlphaEvent, VcfProbeBetaEvent"
    assert "'type'" not in message
    assert later.rewinds == 1


def test_drain_names_the_only_wrong_placeholder_as_the_cause(monkeypatch):
    """With exactly one known wrong placeholder the attribution is certain, so
    the name is still reported on its own."""
    monkeypatch.setattr(collector_events, "_wrong_placeholders", {"VcfProbeSoloEvent"})

    class LaterScan:
        def RewindCollector(self):
            pass

        def ReadNextEvents(self, size):
            raise KeyError("type")

        def DestroyCollector(self):
            pass

    with pytest.raises(KeyError) as excinfo:
        _drain(LaterScan(), "ReadNextEvents")
    assert excinfo.value.args == ("VcfProbeSoloEvent",)


def test_capture_logs_the_minimum_window_cap(monkeypatch, caplog, tmp_path):
    """#27: hitting the vCenter item cap in the smallest window is logged, not
    just recorded."""
    from app import db
    from app.events import service
    from app.events import store as events_store
    from app.models.event import Event, EventPolicy

    db.reset_for_tests(str(tmp_path / "events.db"))
    events_store.ensure_schema()
    events_store.set_event_policy(EventPolicy(retention_hours=2, row_cap=250_000))
    monkeypatch.setattr(service, "MAX_ITEMS", 2)
    now = datetime(2026, 9, 9, 4, 50, tzinfo=UTC)
    rows = [
        Event(id=f"c1:{i}", connection_id="c1", time=now, type="Burst", message="same second")
        for i in range(5)
    ]

    def collect(since, until):
        return [e for e in rows if since < e.time <= until][: service.MAX_ITEMS]

    with caplog.at_level(logging.INFO, logger="vcf_doctor.events"):
        got, complete = service._fetch_window("c1", collect, now - timedelta(seconds=4), now)
    assert complete is False
    assert len(got) >= 2
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("capture limit of 2 reached in the minimum window" in m for m in warnings)
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("splitting it" in m for m in infos)
    status = events_store.capture_status("c1")
    assert status.incomplete_intervals
