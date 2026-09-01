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
    # Snapshot retention default; overridable in the GUI.
    default_retention: int = 96
    # Scheduler floor in minutes.
    min_interval_minutes: int = 5


settings = Settings()
