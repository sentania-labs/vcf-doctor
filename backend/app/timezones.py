"""The timezone the product calls "local".

Retention thins old snapshots down to one per day, and the Snapshots page
groups snapshots under the operator's local calendar day. Those two have to
agree, or the kept daily snapshot shows up under the wrong header (issue #28).
The retention policy therefore carries a timezone; empty means "whatever the
server is set to", which is what a fresh install runs with.
"""

import logging
import os
from datetime import UTC, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("vcf_doctor.timezones")

_ZONEINFO_DIR = "/usr/share/zoneinfo/"


def is_valid(name: str) -> bool:
    """True when name is an IANA zone this machine knows (empty is valid: server default)."""
    if not name:
        return True
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def server_timezone() -> str:
    """The server's IANA zone name: TZ, then /etc/timezone, then /etc/localtime.

    Containers usually run UTC; a host that has been set to a real zone reports
    it here, and that is what the Settings control offers as the default.
    """
    env = (os.environ.get("TZ") or "").strip()
    if env and is_valid(env):
        return env
    try:
        text = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if text and is_valid(text):
            return text
    except OSError:
        pass
    try:
        target = os.path.realpath("/etc/localtime")
        if _ZONEINFO_DIR in target:
            name = target.split(_ZONEINFO_DIR, 1)[1]
            if is_valid(name):
                return name
    except OSError:
        pass
    return "UTC"


def zone(name: str | None) -> tzinfo:
    """The tzinfo for a stored name; empty means the server zone. Unknown names
    fall back to UTC with a warning rather than breaking a retention pass."""
    wanted = (name or "").strip() or server_timezone()
    try:
        return ZoneInfo(wanted)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown timezone %r, using UTC", wanted)
        return UTC
