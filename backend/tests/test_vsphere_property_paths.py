"""Every property path we request must exist on the declared vSphere type.

The PropertyCollector resolves dotted paths against the property's declared
type, not its runtime subclass, so a path that looks right in the docs can
still fault with vmodl.query.InvalidProperty on a live vCenter. pyVmomi
ships the type metadata, so this is checkable offline.
"""

import pytest
from pyVmomi import vim

from app.collectors.vsphere.normalize import PROPERTY_SPECS


def _resolve(vim_type, path: str) -> str | None:
    cur = vim_type
    for part in path.split("."):
        info = None
        for klass in cur.__mro__:
            props = getattr(klass, "_propInfo", None)
            if props and part in props:
                info = props[part]
                break
        if info is None:
            return f"'{part}' does not exist on {cur.__name__}"
        cur = info.type
    return None


@pytest.mark.parametrize(
    ("kind", "path"),
    [(kind, path) for kind, paths in PROPERTY_SPECS.items() for path in paths],
)
def test_property_path_is_valid_for_type(kind: str, path: str):
    problem = _resolve(getattr(vim, kind), path)
    assert problem is None, f"{kind}.{path}: {problem}"
