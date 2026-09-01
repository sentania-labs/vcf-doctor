"""Fake pyVmomi events and tasks (SimpleNamespace) -> Event."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace as NS

from app.collectors.vsphere.events import (
    classify_event,
    is_system_user,
    map_event,
    map_task,
    resolve_entity,
)

NS_ID = "conn1"
T = datetime(2026, 8, 31, 6, 15, 0, tzinfo=UTC)


class Ref:
    """Stand-in for a pyVmomi managed object reference."""

    def __init__(self, wsdl: str, moid: str):
        self._wsdlName = wsdl
        self._moId = moid


class VmPoweredOffEvent(NS):
    pass


class HostConnectionLostEvent(NS):
    pass


class AlarmStatusChangedEvent(NS):
    pass


class DrsVmMigratedEvent(NS):
    pass


class EventEx(NS):
    pass


class DVPortgroupReconfiguredEvent(NS):
    pass


class UserLoginSessionEvent(NS):
    pass


def _base(key: int, msg: str, user: str | None = None, **entities) -> dict:
    d = dict(key=key, createdTime=T, fullFormattedMessage=msg, userName=user)
    d.update({k: None for k in ("vm", "host", "ds", "net", "dvs", "computeResource", "datacenter")})
    d.update(entities)
    return d


def test_vm_power_off_by_admin_is_user_and_maps_to_vm_id():
    ev = VmPoweredOffEvent(
        **_base(
            101,
            "web03 on esx01 in dc is powered off",
            "administrator@vsphere.local",
            vm=NS(vm=Ref("VirtualMachine", "vm-13"), name="web03"),
            host=NS(host=Ref("HostSystem", "host-1"), name="esx01"),
        )
    )
    e = map_event(ev, NS_ID)
    assert e.id == "conn1:101"
    assert e.connection_id == NS_ID
    assert e.source == "event"
    assert e.type == "VmPoweredOffEvent"
    assert e.category == "user"
    assert e.user == "administrator@vsphere.local"
    assert e.resource_id == "vm:conn1:vm-13"
    assert e.resource_name == "web03"
    assert e.resource_type == "vm"
    assert e.time == T


def test_time_is_normalized_to_utc():
    plus_two = timezone(timedelta(hours=2))
    ev = VmPoweredOffEvent(**_base(1, "x", createdTime=T.astimezone(plus_two)))
    assert map_event(ev, NS_ID).time == T


def test_connection_lost_is_error_and_host_id():
    ev = HostConnectionLostEvent(
        **_base(
            102,
            "Host esx03 in dc is not responding",
            host=NS(host=Ref("HostSystem", "host-3"), name="esx03"),
        )
    )
    e = map_event(ev, NS_ID)
    assert e.category == "error"
    assert e.resource_id == "host:conn1:host-3"
    assert e.resource_type == "host"
    assert e.user is None


def test_alarm_red_is_error_yellow_is_warning_via_entity_argument():
    red = AlarmStatusChangedEvent(
        **_base(103, "Alarm went red", **{"from": "green", "to": "red"}),
        entity=NS(entity=Ref("HostSystem", "host-3"), name="esx03"),
    )
    yellow = AlarmStatusChangedEvent(
        **_base(104, "Alarm went yellow", **{"from": "green", "to": "yellow"}),
        entity=NS(entity=Ref("Datastore", "datastore-9"), name="vsan01"),
    )
    r, y = map_event(red, NS_ID), map_event(yellow, NS_ID)
    assert r.category == "error" and r.resource_id == "host:conn1:host-3"
    assert y.category == "warning" and y.resource_id == "datastore:conn1:datastore-9"
    assert y.resource_type == "datastore"


def test_drs_migration_without_user_is_info():
    ev = DrsVmMigratedEvent(
        **_base(105, "DRS migrated ws-w02", vm=NS(vm=Ref("VirtualMachine", "vm-40"), name="ws-w02"))
    )
    assert map_event(ev, NS_ID).category == "info"


def test_system_users_do_not_count_as_user_category():
    for name in ("vpxuser", "vpxd-extension-3f1b6c2a", "VSPHERE.LOCAL\\vpxd-extension-x", "", None):
        assert is_system_user(name), name
    assert not is_system_user("administrator@vsphere.local")
    assert not is_system_user("WLD01\\netops")
    ev = VmPoweredOffEvent(**_base(106, "vCLS powered on", "vpxd-extension-abc"))
    assert map_event(ev, NS_ID).category == "info"


def test_eventex_uses_event_type_id_and_severity():
    warn = EventEx(
        **_base(107, "HA state changed"),
        eventTypeId="com.vmware.vc.HA.HostStateChangedEvent",
        severity="warning",
    )
    err = EventEx(**_base(108, "boom"), eventTypeId="esx.problem.storage.apd", severity="error")
    w, e = map_event(warn, NS_ID), map_event(err, NS_ID)
    assert w.type == "com.vmware.vc.HA.HostStateChangedEvent" and w.category == "warning"
    assert e.type == "esx.problem.storage.apd" and e.category == "error"


def test_network_entity_maps_dvportgroup_to_network_type():
    ev = DVPortgroupReconfiguredEvent(
        **_base(
            109,
            "dvPort group pg-vmotion reconfigured",
            "netops@wld01.vcf.example",
            net=NS(network=Ref("DistributedVirtualPortgroup", "dvportgroup-20"), name="pg-vmotion"),
        )
    )
    e = map_event(ev, NS_ID)
    assert e.resource_id == "network:conn1:dvportgroup-20"
    assert e.resource_type == "network"
    assert e.category == "user"


def test_entity_precedence_vm_before_host_and_none_when_absent():
    ev = NS(**_base(1, "x", vm=NS(vm=Ref("VirtualMachine", "vm-1"), name="a")))
    ev.host = NS(host=Ref("HostSystem", "host-1"), name="h")
    assert resolve_entity(ev, NS_ID)[0] == "vm:conn1:vm-1"
    login = UserLoginSessionEvent(**_base(2, "logged in", "administrator@vsphere.local"))
    e = map_event(login, NS_ID)
    assert e.resource_id is None and e.resource_name is None and e.resource_type is None
    assert e.category == "user"


def test_classify_order_error_beats_user():
    ev = NS(severity=None, to=None)
    assert classify_event(ev, "VmFailedToPowerOffEvent", "administrator@vsphere.local") == "error"
    assert classify_event(ev, "VmPoweredOffEvent", "administrator@vsphere.local") == "user"
    assert classify_event(ev, "VmPoweredOffEvent", None) == "info"
    assert classify_event(NS(severity="warning"), "EventEx", "admin@x") == "warning"


def test_missing_message_falls_back_to_type_name():
    ev = VmPoweredOffEvent(**_base(110, None))
    assert map_event(ev, NS_ID).message == "VmPoweredOffEvent"


def test_task_mapping_success_error_and_system():
    ok = NS(
        key="task-88160",
        descriptionId="VirtualMachine.powerOff",
        description=NS(message="Power Off virtual machine"),
        entity=Ref("VirtualMachine", "vm-13"),
        entityName="web03",
        state="success",
        error=None,
        reason=NS(userName="administrator@vsphere.local"),
        queueTime=T - timedelta(seconds=5),
        startTime=T - timedelta(seconds=4),
        completeTime=T,
    )
    e = map_task(ok, NS_ID)
    assert e.id == "conn1:task:task-88160"
    assert e.source == "task"
    assert e.type == "VirtualMachine.powerOff"
    assert e.category == "user"
    assert e.message == "Power Off virtual machine on web03: success"
    assert e.resource_id == "vm:conn1:vm-13" and e.resource_type == "vm"
    assert e.resource_name == "web03"
    assert e.time == T

    failed = NS(
        key="task-1",
        descriptionId="HostSystem.enterMaintenanceMode",
        description=None,
        entity=Ref("HostSystem", "host-7"),
        entityName="esx07",
        state="error",
        error=NS(localizedMessage="Operation timed out"),
        reason=NS(userName="administrator@vsphere.local"),
        queueTime=None,
        startTime=T,
        completeTime=None,
    )
    f = map_task(failed, NS_ID)
    assert f.category == "error"
    assert f.message.endswith("esx07: error: Operation timed out")
    assert f.time == T

    system = NS(
        key="task-2",
        descriptionId="Drm.ExecuteVMotionLRO",
        description=NS(message="Migrate virtual machine"),
        entity=Ref("VirtualMachine", "vm-40"),
        entityName="ws-w02",
        state="success",
        error=None,
        reason=NS(),  # TaskReasonSystem has no userName
        queueTime=T,
        startTime=None,
        completeTime=None,
    )
    s = map_task(system, NS_ID)
    assert s.category == "info" and s.user is None and s.time == T
