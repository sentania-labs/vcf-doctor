"""Deployment-time configuration.

Everything here comes from environment variables set by whoever deploys the
container. Operator-time configuration (connections, schedules, retention,
assistant settings) lives in SQLite and is edited through the GUI.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VCF_DOCTOR_", extra="ignore")

    db_path: str = "/data/vcf-doctor.db"
    # Test hook, deliberately undocumented: allows a connection of kind
    # "fixture" (bundled snapshot data, no vCenter). Used by the backend test
    # suite and the CI smoke test. Never set it on a real deployment.
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
