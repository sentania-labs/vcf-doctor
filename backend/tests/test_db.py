from app import db


def test_settings_roundtrip():
    db.reset_for_tests()
    assert db.get_setting("retention", 96) == 96
    db.set_setting("retention", 24)
    assert db.get_setting("retention") == 24
    db.set_setting("assistant", {"model": "claude-opus-5"})
    assert db.get_setting("assistant")["model"] == "claude-opus-5"
