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
    if isinstance(exc, vmodl.MethodFault):
        return VSphereError(f"vCenter {host} returned a fault: {exc.msg or type(exc).__name__}")
    return VSphereError(f"unexpected error talking to {host}: {type(exc).__name__}: {exc}")


def to_plain(value: Any) -> Any:
    """Collapse a pyVmomi property value to JSON-friendly Python.

    Managed object references become their moref id string; arrays become
    lists; enums (str subclasses) become plain str; scalars pass through.
    """
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return str(value)
    if hasattr(value, "_moId"):
        return str(value._moId)
    if isinstance(value, list | tuple):
        return [to_plain(v) for v in value]
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
        )
