"""Resolve retention timezones independently of the browser's display timezone.

Operator guidance lives in docs/RETENTION_EVENTS.md.
"""

import logging
from datetime import UTC, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("vcf_doctor.timezones")

def is_valid(name: str) -> bool:
    """True when name is an IANA zone this machine knows."""
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def zone(name: str | None) -> tzinfo:
    """The tzinfo for a stored name, with a safe UTC fallback."""
    wanted = (name or "").strip()
    try:
        return ZoneInfo(wanted)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown timezone %r, using UTC", wanted)
        return UTC
