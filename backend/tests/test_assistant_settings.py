import pytest

from app import db
from app.assistant import settings as assistant_settings
from app.config import settings as cfg

SECRET = "sk-ant-test-not-a-real-key-0000"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db.reset_for_tests(str(tmp_path / "s.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cfg, "demo_mode", False)


def test_defaults_without_anything_stored():
    s = assistant_settings.get_settings()
    assert s.enabled is True
    assert s.provider == "anthropic"
    assert s.model == "claude-opus-5"
    assert s.api_key_set is False


def test_roundtrip_never_returns_key():
    out = assistant_settings.update_settings({"api_key": SECRET, "model": "claude-opus-5"})
    assert out.api_key_set is True
    dumped = out.model_dump()
    assert SECRET not in str(dumped)
    assert "api_key" not in dumped
    # Persisted settings row does not carry the key either.
    assert SECRET not in str(db.get_setting("assistant"))
    # Key is resolvable for the provider.
    assert assistant_settings.resolve_api_key() == SECRET
    assert assistant_settings.get_provider().name == "anthropic"


def test_env_key_wins_over_db(monkeypatch):
    assistant_settings.update_settings({"api_key": SECRET})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-0000")
    assert assistant_settings.resolve_api_key() == "sk-ant-env-0000"
    assert assistant_settings.get_settings().api_key_set is True


def test_clearing_key():
    assistant_settings.update_settings({"api_key": SECRET})
    assistant_settings.update_settings({"api_key": ""})
    assert assistant_settings.get_settings().api_key_set is False


def test_partial_update_keeps_other_fields():
    assistant_settings.update_settings({"model": "claude-sonnet-5"})
    assistant_settings.update_settings({"enabled": False})
    s = assistant_settings.get_settings()
    assert s.model == "claude-sonnet-5" and s.enabled is False and s.provider == "anthropic"


def test_bad_provider_rejected():
    with pytest.raises(Exception):  # noqa: B017
        assistant_settings.update_settings({"provider": "openai"})


def test_no_key_does_not_silently_use_mock():
    p = assistant_settings.get_provider()
    assert p.name == "anthropic"
