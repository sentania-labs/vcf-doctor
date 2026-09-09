"""Deployment-time configuration.

Everything here comes from environment variables set by whoever deploys the
container. Operator-time configuration (connections, schedules, retention,
assistant settings) lives in PostgreSQL and is edited through the GUI.

The database connection is a deployment binding, not a product setting: it is
set here and shown, never edited, in the interface. The database password is
deliberately not an environment variable. It is read from the file named by
`VCF_DOCTOR_DB_PASSWORD_FILE`, which is a compose secret or a mounted
Kubernetes secret at the same path in both shapes.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Where compose mounts its secret and where the Kubernetes manifest mounts
# its Secret volume, so one default covers both shapes.
DEFAULT_DB_PASSWORD_FILE = "/run/secrets/vcf-doctor-db-password"


class PasswordInUrl(ValueError):
    """A password was supplied in the connection URL. Refused on purpose."""


def _retention_timezone_default() -> str:
    return (os.environ.get("TZ") or "UTC").strip() or "UTC"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VCF_DOCTOR_", extra="ignore")

    # libpq URL without a password. `DATABASE_URL` is honoured unprefixed so a
    # generic Postgres environment works unchanged.
    database_url: str = Field(
        default="postgresql://vcf_doctor@postgres:5432/vcf_doctor",
        validation_alias=AliasChoices("VCF_DOCTOR_DATABASE_URL", "DATABASE_URL"),
    )
    # File holding the database password. Missing file means no password is
    # sent, which is what a trust-authenticated local server wants.
    db_password_file: str = DEFAULT_DB_PASSWORD_FILE
    # Writable directory on the persistent volume. Its only remaining job is the
    # generated encryption key file; everything else lives in PostgreSQL.
    data_dir: str = "/data"
    # Connections per worker process. One pool per process; more than one
    # uvicorn worker multiplies this, so keep it well under the server's
    # max_connections.
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    # Seconds a caller waits for a free pooled connection before failing.
    db_pool_timeout: float = 10.0
    # Test-only hook: allows a connection of kind "fixture" (bundled snapshot
    # data, no vCenter). Used by the backend test suite and the CI smoke test;
    # deployment behavior is documented in docs/DEPLOYMENT.md. Never set it on
    # a real deployment.
    test_fixtures: bool = False
    llm_model: str = "claude-opus-5"
    # Directory containing the built frontend (index.html). Empty disables static serving.
    static_dir: str = ""
    # Snapshot retention tier defaults (days); the effective policy lives in
    # the settings table and is edited in the GUI. The old
    # VCF_DOCTOR_DEFAULT_RETENTION count is no longer read.
    retention_recent_days: int = 14
    retention_hourly_days: int = 30
    retention_daily_days: int = 365
    # IANA zone whose midnights are the daily tier's day marks.
    retention_timezone: str = Field(default_factory=_retention_timezone_default)
    # Event history is intentionally independent from snapshot history. The
    # effective values live in the settings table and are editable in the GUI.
    event_retention_hours: int = 48
    event_row_cap: int = 250_000
    # Scheduler floor in minutes.
    min_interval_minutes: int = 5
    # "on" requires the shared operator password; "off" for deployments that
    # front the app with ingress authentication.
    auth: str = "on"
    # Comma-separated IPs or CIDRs allowed to set X-Forwarded-For and
    # X-Forwarded-Proto (the ingress). Overrides the Settings page value.
    # Empty (the default) trusts nobody; see app/proxies.py.
    trusted_proxies: str = ""


settings = Settings()


def database_password() -> str | None:
    """The password from the secret file, or None when there is no file.

    Whitespace at the end is stripped because an editor or a `kubectl create
    secret --from-file` almost always leaves a trailing newline behind.
    """
    path = Path(settings.db_password_file or "")
    if not path.name or not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip("\r\n")
    return value or None


def database_url_without_password(url: str | None = None) -> str:
    """The configured URL, refusing one that carries a password.

    A password in the URL means a password in an environment variable, which
    no supported deployment path may do.
    """
    raw = settings.database_url if url is None else url
    parts = urlsplit(raw)
    if parts.password:
        raise PasswordInUrl(
            "the database password must not be part of VCF_DOCTOR_DATABASE_URL or "
            f"DATABASE_URL; put it in the file named by VCF_DOCTOR_DB_PASSWORD_FILE "
            f"(currently {settings.db_password_file or 'unset'})"
        )
    return urlunsplit(parts)
