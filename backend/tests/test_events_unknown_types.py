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


def _event_response(*returnvals: str) -> bytes:
    """A SOAP ReadNextEvents response envelope around the given returnval elements."""
    body = "\n".join(returnvals)
    return f"""<soapenv:Envelope {XMLNS} xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
<soapenv:Body>
<ReadNextEventsResponse>
{body}
</ReadNextEventsResponse>
</soapenv:Body>
</soapenv:Envelope>""".encode()


def _returnval(key: int, class_name: str) -> str:
    return f"""<returnval xsi:type="{class_name}">
<key>{key}</key><chainId>{key}</chainId><createdTime>2026-09-09T04:50:00Z</createdTime>
<userName>admin</userName><fullFormattedMessage>Probe {key}</fullFormattedMessage>
</returnval>"""


def _unknown_event_class_page(class_name: str) -> bytes:
    """A response whose only event has an event class pyVmomi does not define."""
    return _event_response(_returnval(103, class_name))


def _captured_multi_event_page(unknown_name: str) -> bytes:
    """A captured response with two known events and one unknown event class."""
    return _event_response(
        _returnval(201, "VmPoweredOnEvent"),
        _returnval(202, unknown_name),
        _returnval(203, "VmPoweredOffEvent"),
    )


def _read_events(page: bytes) -> list:
    """Deserialize a response the way pyVmomi does for a ReadNextEvents call."""
    from pyVmomi import SoapAdapter, VmomiSupport

    array_type = VmomiSupport.GetVmodlType("vim.event.Event[]")
    return list(SoapAdapter.SoapResponseDeserializer(None).Deserialize(page, array_type) or [])


def test_drain_drops_unknown_event_class_and_logs_warning(caplog):
    """An unknown event class in a single-event page is dropped during pre-scan.
    No placeholder is registered, pyVmomi's registry stays clean, and one warning
    is logged per drain naming the unknown type and count."""
    name = f"VcfProbeSoloEvent{uuid4().hex}"
    page = _unknown_event_class_page(name)

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
            return _read_events(page)

        def DestroyCollector(self):
            pass

    collector = Collector()
    with caplog.at_level(logging.WARNING, logger=collector_events.__name__):
        batch = _drain(collector, "ReadNextEvents")

    assert batch.items == []
    assert batch.complete is True
    assert collector.rewinds == 1
    assert name not in placeholder_types()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("dropped 1 event(s) of unknown type(s): " + name in m for m in warnings)


def test_drain_drops_unknown_event_from_captured_multi_event_page(caplog):
    """A captured page with mixed known and unknown events drops the unknown event,
    returns the valid events, and logs one warning naming the dropped type."""
    name = f"VcfProbeMultiEvent{uuid4().hex}"
    page = _captured_multi_event_page(name)

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
            return _read_events(page)

        def DestroyCollector(self):
            pass

    collector = Collector()
    with caplog.at_level(logging.WARNING, logger=collector_events.__name__):
        batch = _drain(collector, "ReadNextEvents")

    assert len(batch.items) == 2
    assert [item.key for item in batch.items] == [201, 203]
    assert batch.complete is True
    assert name not in placeholder_types()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("dropped 1 event(s) of unknown type(s): " + name in m for m in warnings)


def test_drain_drops_unknown_sibling_from_prefixed_page():
    """When a page uses namespace-prefixed xsi:type values (e.g. vim:VmPoweredOnEvent),
    filtering an unknown sibling preserves the namespace declaration so pyVmomi resolves
    the surviving event type without error."""
    unknown = f"VcfProbePrefixedSiblingEvent{uuid4().hex}"
    page = (
        b'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        b'xmlns:vim="urn:vim25" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        b"<soapenv:Body>\n"
        b"<ReadNextEventsResponse>\n"
        b'<returnval xsi:type="vim:VmPoweredOnEvent">\n'
        b"<key>301</key><chainId>301</chainId><createdTime>2026-09-09T04:50:00Z</createdTime>\n"
        b"<userName>admin</userName><fullFormattedMessage>Probe 301</fullFormattedMessage>\n"
        b"</returnval>\n"
        b'<returnval xsi:type="vim:' + unknown.encode() + b'">\n'
        b"<key>302</key><chainId>302</chainId><createdTime>2026-09-09T04:50:00Z</createdTime>\n"
        b"</returnval>\n"
        b'<returnval xsi:type="vim:VmPoweredOffEvent">\n'
        b"<key>303</key><chainId>303</chainId><createdTime>2026-09-09T04:50:00Z</createdTime>\n"
        b"<userName>admin</userName><fullFormattedMessage>Probe 303</fullFormattedMessage>\n"
        b"</returnval>\n"
        b"</ReadNextEventsResponse>\n"
        b"</soapenv:Body>\n"
        b"</soapenv:Envelope>"
    )

    class Collector:
        def __init__(self):
            self.served = False

        def RewindCollector(self):
            self.served = False

        def ReadNextEvents(self, size):
            if self.served:
                return []
            self.served = True
            return _read_events(page)

        def DestroyCollector(self):
            pass

    collector = Collector()
    batch = _drain(collector, "ReadNextEvents")
    assert batch.complete is True
    assert len(batch.items) == 2
    assert [item.key for item in batch.items] == [301, 303]
    assert unknown not in placeholder_types()

    # pyVmomi ignores the prefix, so also check the serialized bytes: every
    # xsi:type QName prefix in the filtered page must resolve to a declared namespace.
    import io
    import xml.etree.ElementTree as ET

    from app.collectors.vsphere.events import prescan_and_filter

    filtered, dropped = prescan_and_filter(page)
    assert dropped == [unknown]
    declared = {
        prefix for _, (prefix, _uri) in ET.iterparse(io.BytesIO(filtered), events=["start-ns"])
    }
    xsi_type = "{http://www.w3.org/2001/XMLSchema-instance}type"
    prefixes = [
        el.attrib[xsi_type].split(":", 1)[0]
        for el in ET.fromstring(filtered).iter()
        if ":" in el.attrib.get(xsi_type, "")
    ]
    assert len(prefixes) == 2
    assert set(prefixes) <= declared


def test_prescan_and_filter_direct():
    """Direct verification of prescan_and_filter edge cases."""
    from app.collectors.vsphere.events import prescan_and_filter

    # 1. Plain XML without xsi:type returns untouched
    plain = b"<obj><key>1</key></obj>"
    assert prescan_and_filter(plain) == (plain, [])

    # 2. Invalid XML returns untouched
    bad = b"<not valid xml"
    assert prescan_and_filter(bad) == (bad, [])

    # 3. Known type returns untouched
    known = (
        b'<obj xmlns="urn:vim25" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        b'xsi:type="VmPoweredOnEvent"><key>1</key></obj>'
    )
    assert prescan_and_filter(known) == (known, [])


def _moref_property_response() -> bytes:
    return f"""<soapenv:Envelope {XMLNS} xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
<soapenv:Body>
<RetrievePropertiesExResponse>
<returnval>
<objects>
<obj type="VirtualMachine">vm-1</obj>
<propSet>
<name>datastore</name>
<val xsi:type="ArrayOfManagedObjectReference">
<ManagedObjectReference type="Datastore" xsi:type="ManagedObjectReference">
datastore-1</ManagedObjectReference>
</val>
</propSet>
<propSet>
<name>parent</name>
<val type="Folder" xsi:type="ManagedObjectReference">group-v3</val>
</propSet>
</objects>
</returnval>
</RetrievePropertiesExResponse>
</soapenv:Body>
</soapenv:Envelope>""".encode()


def test_moref_valued_property_response_is_never_filtered():
    """Inventory style responses carry xsi:type="ManagedObjectReference" values.
    They are not event classes and must reach pyVmomi intact, both outside any
    drain and inside one."""
    from pyVmomi import SoapAdapter, VmomiSupport

    from app.collectors.vsphere.events import prescan_and_filter

    page = _moref_property_response()
    assert prescan_and_filter(page) == (page, [])

    result_type = VmomiSupport.GetVmodlType("vmodl.query.PropertyCollector.RetrieveResult")
    for stats in (None, {}):
        token = collector_events._drain_dropped.set(stats)
        try:
            result = SoapAdapter.SoapResponseDeserializer(None).Deserialize(page, result_type)
        finally:
            collector_events._drain_dropped.reset(token)
        assert result is not None
        assert [o.obj._moId for o in result.objects] == ["vm-1"]
        assert not stats


def test_hook_leaves_responses_alone_outside_a_drain():
    """Outside _drain the hook is a pass-through: an unknown event class is not
    pre-scanned or dropped, pyVmomi sees the original bytes."""
    name = f"VcfProbeOutsideEvent{uuid4().hex}"
    with pytest.raises(KeyError):
        _read_events(_unknown_event_class_page(name))


def test_read_pages_does_not_end_the_window_on_a_page_of_only_unknown_events():
    """A page whose events are all dropped comes back empty. That is not the end
    of the window: later pages still have to be read."""
    unknown = _unknown_event_class_page(f"VcfProbeGapEvent{uuid4().hex}")
    known = _event_response(_returnval(101, "VmPoweredOnEvent"))

    class Collector:
        def __init__(self):
            self.pages = [unknown, unknown, known, None]

        def RewindCollector(self):
            pass

        def ReadNextEvents(self, size):
            page = self.pages.pop(0)
            if page is None:
                return []
            return _read_events(page)

        def DestroyCollector(self):
            pass

    collector = Collector()
    batch = _drain(collector, "ReadNextEvents")
    assert [item.key for item in batch.items] == [101]
    assert batch.complete is True
    assert collector.pages == []


def test_capture_logs_the_minimum_window_cap(monkeypatch, caplog, tmp_path):
    """#27: hitting the vCenter item cap in the smallest window is logged, not
    just recorded."""
    from app import db
    from app.events import service
    from app.events import store as events_store
    from app.models.event import Event, EventPolicy

    db.reset_for_tests()
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
