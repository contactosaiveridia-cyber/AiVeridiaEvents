from fastapi.testclient import TestClient


def test_health(monkeypatch):
    monkeypatch.setenv("AIV_CHECKPOINTER", "memory")
    from api.main import app

    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
