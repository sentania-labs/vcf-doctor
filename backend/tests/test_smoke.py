from fastapi.testclient import TestClient

from app.main import app
from app.models import Resource


def test_health():
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_resource_roundtrip():
    r = Resource(id="host:vc01:esx03", type="host", name="esx03", source="vcenter:vc01")
    assert Resource.model_validate_json(r.model_dump_json()) == r
