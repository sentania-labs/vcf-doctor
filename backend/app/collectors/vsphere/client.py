"""pyVmomi session handling and bulk property retrieval.

Everything that touches the vSphere SDK lives here. The output is a
`RawInventory` of plain Python values that `normalize.py` turns into
Resources. Collection uses one ContainerView + PropertyCollector call per
managed object type with an explicit property list, so a 2,000 VM vCenter is
a handful of round trips rather than thousands.
"""

from __future__ import annotations

import socket
import ssl
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pyVim import connect as pyvim_connect
from pyVmomi import vim, vmodl

from app.collectors.vsphere.normalize import PROPERTY_SPECS, RawInventory, RawObject

DEFAULT_TIMEOUT_SECONDS = 30


class VSphereError(Exception):
    """Raised by the collector when vCenter cannot be reached, authenticated
    against, or queried. The message is safe to show an operator."""


class VSphereAuthError(VSphereError):
    """Bad username or password."""


class VSphereTLSError(VSphereError):
    """Certificate verification failed (verify_tls is on and the vCenter
    certificate is not trusted by this container)."""


class VSphereUnreachableError(VSphereError):
    """DNS, TCP or timeout failure before any vSphere API call happened."""


# wsdl type name -> pyVmomi managed object class
_VIM_TYPES: dict[str, type] = {
    "Datacenter": vim.Datacenter,
    "Folder": vim.Folder,
    "ComputeResource": vim.ComputeResource,
    "ClusterComputeResource": vim.ClusterComputeResource,
    "HostSystem": vim.HostSystem,
    "VirtualMachine": vim.VirtualMachine,
    "Datastore": vim.Datastore,
    "Network": vim.Network,
    "DistributedVirtualPortgroup": vim.dvs.DistributedVirtualPortgroup,
    "OpaqueNetwork": vim.OpaqueNetwork,
    "DistributedVirtualSwitch": vim.DistributedVirtualSwitch,
    "ResourcePool": vim.ResourcePool,
}


def build_ssl_context(verify_tls: bool) -> ssl.SSLContext:
    if verify_tls:
        return ssl.create_default_context()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def split_host_port(host: str, default_port: int = 443) -> tuple[str, int]:
    """Accept "vc01", "vc01:8443" or "[::1]:443"."""
    h = host.strip()
    if h.startswith("["):
        addr, _, rest = h[1:].partition("]")
        port = int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else default_port
        return addr, port
    if h.count(":") == 1:
        addr, _, p = h.partition(":")
        if p.isdigit():
            return addr, int(p)
    return h, default_port


def classify_exception(exc: BaseException, host: str) -> VSphereError:
    """Map SDK / socket / ssl exceptions onto our error classes with an
    operator-readable message."""
    if isinstance(exc, VSphereError):
        return exc
    if isinstance(exc, vim.fault.InvalidLogin):
        return VSphereAuthError(f"vCenter {host} rejected the username or password")
    if isinstance(exc, vim.fault.NoPermission):
        return VSphereAuthError(f"account lacks permission on vCenter {host}: {exc.msg}")
    if isinstance(exc, ssl.SSLCertVerificationError):
        detail = getattr(exc, "verify_message", None) or exc
        return VSphereTLSError(
            f"TLS certificate verification failed for {host}: {detail}. "
            "Trust the certificate or turn off TLS verification for this connection."
        )
    if isinstance(exc, ssl.SSLError):
        return VSphereTLSError(f"TLS handshake with {host} failed: {exc}")
    if isinstance(exc, socket.gaierror):
        return VSphereUnreachableError(f"cannot resolve hostname {host}")
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return VSphereUnreachableError(f"connection to {host} timed out")
    if isinstance(exc, ConnectionRefusedError):
        return VSphereUnreachableError(f"connection to {host} refused")
    if isinstance(exc, OSError):
        return VSphereUnreachableError(f"cannot reach {host}: {exc.strerror or exc}")
    if isinstance(exc, vmodl.query.InvalidProperty):
        # Our property list asked for a path the type does not have. Name it so
        # the operator sees a collector bug, not a vCenter problem.
        return VSphereError(
            f"collector requested an invalid property '{exc.name}' from {host}; "
            "this is a VCF Doctor bug, please report it"
        )
    if isinstance(exc, vmodl.MethodFault):
        return VSphereError(f"vCenter {host} returned a fault: {exc.msg or type(exc).__name__}")
    return VSphereError(f"unexpected error talking to {host}: {type(exc).__name__}: {exc}")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _moid(value: Any) -> str | None:
    return None if value is None else str(getattr(value, "_moId", value))


def _flatten_vnic(v: Any) -> dict[str, Any]:
    spec = getattr(v, "spec", None)
    ip = getattr(spec, "ip", None)
    dvport = getattr(spec, "distributedVirtualPort", None)
    return {
        "device": to_plain(getattr(v, "device", None)),
        "ip": to_plain(getattr(ip, "ipAddress", None)),
        "mtu": to_plain(getattr(spec, "mtu", None)),
        "portgroup": to_plain(getattr(v, "portgroup", None)) or None,
        "portgroupKey": to_plain(getattr(dvport, "portgroupKey", None)),
    }


def _flatten_pnic(p: Any) -> dict[str, Any]:
    link = getattr(p, "linkSpeed", None)
    return {
        "device": to_plain(getattr(p, "device", None)),
        "mac": to_plain(getattr(p, "mac", None)),
        "linkSpeedMb": to_plain(getattr(link, "speedMb", None)),
    }


def _flatten_device(d: Any) -> dict[str, Any] | None:
    """VirtualDisk and VirtualEthernetCard subclasses only; everything else
    (controllers, CD-ROMs, video cards) is dropped."""
    info = getattr(d, "deviceInfo", None)
    backing = getattr(d, "backing", None)
    if isinstance(d, vim.vm.device.VirtualDisk):
        cap = getattr(d, "capacityInBytes", None)
        if cap is None and getattr(d, "capacityInKB", None) is not None:
            cap = int(d.capacityInKB) * 1024
        return {
            "kind": "disk",
            "label": to_plain(getattr(info, "label", None)),
            "capacityBytes": None if cap is None else int(cap),
            "datastore": _moid(getattr(backing, "datastore", None)),
            "thin": to_plain(getattr(backing, "thinProvisioned", None)),
        }
    if isinstance(d, vim.vm.device.VirtualEthernetCard):
        port = getattr(backing, "port", None)
        conn = getattr(d, "connectable", None)
        return {
            "kind": "nic",
            "label": to_plain(getattr(info, "label", None)),
            "mac": to_plain(getattr(d, "macAddress", None)),
            "network": _moid(getattr(backing, "network", None)),
            "portgroupKey": to_plain(getattr(port, "portgroupKey", None)),
            "opaqueNetworkId": to_plain(getattr(backing, "opaqueNetworkId", None)),
            "connected": to_plain(getattr(conn, "connected", None)),
        }
    return None


def _flatten_snapshot(node: Any) -> dict[str, Any]:
    return {
        "name": to_plain(getattr(node, "name", None)),
        "createTime": _iso(getattr(node, "createTime", None)),
        "children": [
            _flatten_snapshot(c) for c in (getattr(node, "childSnapshotList", None) or [])
        ],
    }


def _flatten_vlan(spec: Any) -> dict[str, Any] | None:
    vmw = vim.dvs.VmwareDistributedVirtualSwitch
    if isinstance(spec, vmw.TrunkVlanSpec):
        ranges = [
            [int(r.start), int(r.end)]
            for r in (spec.vlanId or [])
            if getattr(r, "start", None) is not None
        ]
        return {"kind": "trunk", "ranges": ranges}
    if isinstance(spec, vmw.PvlanSpec):
        return {"kind": "pvlan", "pvlanId": to_plain(spec.pvlanId)}
    if isinstance(spec, vmw.VlanIdSpec):
        return {"kind": "id", "vlanId": to_plain(spec.vlanId)}
    return None


def _flatten_data_object(value: Any) -> Any:
    """Targeted flatteners for the DataObjects we request whole or in lists.
    Returns the sentinel `_SKIP` for list members that carry nothing we need."""
    if isinstance(value, vim.host.VirtualNic):
        return _flatten_vnic(value)
    if isinstance(value, vim.host.PhysicalNic):
        return _flatten_pnic(value)
    if isinstance(value, vim.host.VirtualSwitch):
        return {"name": to_plain(value.name)}
    if isinstance(value, vim.vm.device.VirtualDevice):
        flat = _flatten_device(value)
        return _SKIP if flat is None else flat
    if isinstance(value, vim.vm.SnapshotTree):
        return _flatten_snapshot(value)
    if isinstance(value, vim.Datastore.HostMount):
        mi = getattr(value, "mountInfo", None)
        return {
            "host": _moid(getattr(value, "key", None)),
            "mounted": to_plain(getattr(mi, "mounted", None)),
            "accessible": to_plain(getattr(mi, "accessible", None)),
        }
    if isinstance(value, vim.Datastore.Info):
        vmfs = (
            getattr(value, "vmfs", None) if isinstance(value, vim.host.VmfsDatastoreInfo) else None
        )
        return {"vmfsVersion": to_plain(getattr(vmfs, "version", None))}
    if isinstance(value, vim.ComputeResource.Summary):
        return {
            "currentEVCModeKey": to_plain(getattr(value, "currentEVCModeKey", None)),
            "totalCpu": to_plain(getattr(value, "totalCpu", None)),
            "totalMemory": to_plain(getattr(value, "totalMemory", None)),
            "numHosts": to_plain(getattr(value, "numHosts", None)),
        }
    if isinstance(value, vim.cluster.RuleInfo):
        return {"name": to_plain(value.name), "enabled": to_plain(value.enabled)}
    if isinstance(value, vim.dvs.DistributedVirtualPort.Setting):
        return {"vlan": _flatten_vlan(getattr(value, "vlan", None))}
    if isinstance(value, vim.Network.Summary):
        return {
            "opaqueNetworkType": to_plain(getattr(value, "opaqueNetworkType", None)),
            "opaqueNetworkId": to_plain(getattr(value, "opaqueNetworkId", None)),
        }
    return None


_SKIP = object()


def to_plain(value: Any) -> Any:
    """Collapse a pyVmomi property value to JSON-friendly Python.

    Managed object references become their moref id string; arrays become
    lists; enums (str subclasses) become plain str; datetimes become ISO
    strings; the DataObjects listed in normalize.py become small dicts;
    scalars pass through.
    """
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "_moId"):
        return str(value._moId)
    if isinstance(value, list | tuple):
        out = [to_plain(v) for v in value]
        return [v for v in out if v is not _SKIP]
    flat = _flatten_data_object(value)
    if flat is not None:
        return flat
    if hasattr(value, "_wsdlName"):
        # A DataObject we did not expect; keep something readable.
        return str(value)
    return str(value)


def tcp_preflight(addr: str, port: int, timeout: float) -> None:
    """Open and close a TCP connection. Raises OSError on failure."""
    with socket.create_connection((addr, port), timeout=timeout):
        pass


class VSphereSession:
    """Context manager around a pyVmomi ServiceInstance."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_tls: bool = False,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.verify_tls = verify_tls
        self.timeout = timeout
        self.si: Any = None

    def __enter__(self) -> VSphereSession:
        addr, port = split_host_port(self.host)
        # pyVmomi's SmartConnect does not honour httpConnectionTimeout on its
        # initial version probe, so a blackholed address hangs for minutes.
        # A cheap TCP pre-flight turns that into a prompt, readable failure.
        try:
            tcp_preflight(addr, port, min(self.timeout, 10))
        except OSError as exc:
            raise classify_exception(exc, self.host) from exc
        try:
            self.si = pyvim_connect.SmartConnect(
                host=addr,
                port=port,
                user=self.username,
                pwd=self.password,
                sslContext=build_ssl_context(self.verify_tls),
                httpConnectionTimeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001  (classified below)
            raise classify_exception(exc, self.host) from exc
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self.si is not None:
            try:
                pyvim_connect.Disconnect(self.si)
            except Exception:  # noqa: BLE001  best effort teardown
                pass
            self.si = None

    # ---- about ----------------------------------------------------------------

    def about(self) -> dict[str, str | None]:
        about = self.si.RetrieveContent().about
        return {
            "name": getattr(about, "name", None) or self.host,
            "fullName": getattr(about, "fullName", None),
            "version": getattr(about, "version", None),
            "build": getattr(about, "build", None),
            "instanceUuid": getattr(about, "instanceUuid", None),
            "apiType": getattr(about, "apiType", None),
            "apiVersion": getattr(about, "apiVersion", None),
            "osType": getattr(about, "osType", None),
        }

    # ---- bulk retrieval ---------------------------------------------------------

    def retrieve(self, wsdl_type: str, paths: Iterable[str]) -> list[RawObject]:
        """One ContainerView + PropertyCollector pass for a single type."""
        content = self.si.RetrieveContent()
        vim_type = _VIM_TYPES[wsdl_type]
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim_type], True)
        try:
            pc = content.propertyCollector
            q = vmodl.query.PropertyCollector
            traversal = q.TraversalSpec(
                name="traverseView", path="view", skip=False, type=vim.view.ContainerView
            )
            obj_spec = q.ObjectSpec(obj=view, skip=True, selectSet=[traversal])
            prop_spec = q.PropertySpec(type=vim_type, pathSet=list(paths), all=False)
            filter_spec = q.FilterSpec(objectSet=[obj_spec], propSet=[prop_spec])
            result = pc.RetrievePropertiesEx([filter_spec], q.RetrieveOptions())
            out: list[RawObject] = []
            while result is not None:
                for oc in result.objects:
                    props = {p.name: to_plain(p.val) for p in (oc.propSet or [])}
                    out.append(
                        RawObject(moref=str(oc.obj._moId), kind=str(oc.obj._wsdlName), props=props)
                    )
                if not result.token:
                    break
                result = pc.ContinueRetrievePropertiesEx(result.token)
            return out
        finally:
            try:
                view.DestroyView()
            except Exception:  # noqa: BLE001  best effort teardown
                pass

    def inventory(self) -> RawInventory:
        about = self.about()
        merged: dict[str, RawObject] = {}
        try:
            for wsdl_type, paths in PROPERTY_SPECS.items():
                for raw in self.retrieve(wsdl_type, paths):
                    existing = merged.get(raw.moref)
                    if existing is None:
                        merged[raw.moref] = raw
                    else:
                        # Same object seen via a base-type view (e.g. a cluster
                        # through the ComputeResource view). Keep the most
                        # specific kind and union the properties.
                        existing.props.update(raw.props)
                        existing.kind = raw.kind
        except Exception as exc:  # noqa: BLE001  (classified)
            raise classify_exception(exc, self.host) from exc
        return RawInventory(
            host=self.host,
            name=about["name"] or self.host,
            version=about["version"],
            build=about["build"],
            instance_uuid=about["instanceUuid"],
            objects=list(merged.values()),
            api_version=about.get("apiVersion"),
            os_type=about.get("osType"),
        )
