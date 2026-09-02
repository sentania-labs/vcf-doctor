"""Deployment-time configuration.

Everything here comes from environment variables set by whoever deploys the
container. Operator-time configuration (connections, schedules, retention,
assistant settings) lives in SQLite and is edited through the GUI.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VCF_DOCTOR_", extra="ignore")

    db_path: str = "/data/vcf-doctor.db"
    demo_mode: bool = False
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


settings = Settings()
