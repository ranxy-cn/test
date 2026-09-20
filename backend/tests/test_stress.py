from __future__ import annotations


class _FakeProc:
    def __init__(self) -> None:
        self.terminated = False

    def is_alive(self) -> bool:
        return not self.terminated

    def terminate(self) -> None:
        self.terminated = True

    def join(self, timeout: float = 0) -> None:  # noqa: ARG002
        return None


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("STRESS_TOOLS_ENABLED", "true")
    from app.config import get_settings

    get_settings.cache_clear()


def test_status_disabled_by_default(client):
    resp = client.get("/api/v1/tools/cpu-stress")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["running"] is False


def test_start_rejected_when_disabled(client):
    resp = client.post("/api/v1/tools/cpu-stress", json={"duration_seconds": 60})
    assert resp.status_code == 403
    assert "STRESS_TOOLS_ENABLED" in resp.json()["detail"]


def test_status_endpoint_requires_no_secret(client):
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    assert resp.json()["integrations"]["stress_tools_enabled"] is False


def test_start_and_stop_with_enabled(client, monkeypatch):
    _enable(monkeypatch)
    from app.services import stress

    def fake_spawn(cores, deadline):  # noqa: ARG001
        return [_FakeProc(), _FakeProc()]

    monkeypatch.setattr(stress, "_spawn", fake_spawn)

    resp = client.post("/api/v1/tools/cpu-stress", json={"duration_seconds": 60})
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["cores"] >= 1
    assert 0 < body["seconds_remaining"] <= 60

    resp = client.post("/api/v1/tools/cpu-stress/stop")
    assert resp.status_code == 200
    assert resp.json()["stopped"] == 2
    assert client.get("/api/v1/tools/cpu-stress").json()["running"] is False


def test_start_clamps_duration_to_max(client, monkeypatch):
    _enable(monkeypatch)
    from app.config import get_settings
    from app.services import stress

    monkeypatch.setattr(stress, "_spawn", lambda cores, deadline: [_FakeProc()])
    resp = client.post("/api/v1/tools/cpu-stress", json={"duration_seconds": 10_000_000})
    assert resp.status_code == 200
    limit = int(get_settings().stress_max_seconds)
    assert resp.json()["seconds_remaining"] <= limit
    client.post("/api/v1/tools/cpu-stress/stop")
