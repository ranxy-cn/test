"""页面一键重部 agent 端点：已存密码复用 / 可选覆盖 / 母机落到本机子机（不真连 SSH，spawn 打桩）。"""
from __future__ import annotations

import pytest

from app.models import Asset
from app.security import decrypt_secret, encrypt_secret


def _mk(
    aid: str,
    ip: str = "1.2.3.4",
    pwd: str = "old-pass",
    kind: str = "child",
    self_child: str = "",
    lang: str = "",
) -> Asset:
    extra: dict = {}
    if ip:
        extra["provision"] = {"ip": ip, "port": 22, "username": "root"}
    if self_child:
        extra["self_child_id"] = self_child
    if lang:
        extra["agent_deploy"] = {"lang": lang, "state": "success"}
    return Asset(
        id=aid,
        hostname=aid,
        app="演示App",
        role="app",
        env="prod",
        owner="张三",
        tenant_id="tenant-default",
        kind=kind,
        ssh_password_enc=encrypt_secret(pwd) if pwd else "",
        extra=extra,
    )


@pytest.fixture
def no_spawn(monkeypatch):
    captured: dict = {}

    def fake_spawn(asset_id, **kw):
        captured["asset_id"] = asset_id
        captured.update(kw)

    monkeypatch.setattr("app.api._spawn_agent_deploy", fake_spawn)
    return captured


def test_redeploy_uses_stored_password_and_lang_default(client, db, no_spawn):
    db.add(_mk("nd-rd-1"))
    db.commit()
    resp = client.post("/api/v1/assets/nd-rd-1/agent/deploy", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "running" and resp.json()["lang"] == "go"  # 无历史部署默认 go
    assert no_spawn["asset_id"] == "nd-rd-1"
    assert no_spawn["password"] == "old-pass"
    assert no_spawn["port"] == 22 and no_spawn["username"] == "root"
    row = db.get(Asset, "nd-rd-1")
    assert row.extra["provision"]["status"] == "running"
    assert row.extra["agent_deploy"]["state"] == "running"


def test_redeploy_keeps_previous_lang(client, db, no_spawn):
    db.add(_mk("nd-rd-2", lang="py"))
    db.commit()
    resp = client.post("/api/v1/assets/nd-rd-2/agent/deploy", json={})
    assert resp.status_code == 200 and resp.json()["lang"] == "py"  # 沿用上次部署语言


def test_redeploy_explicit_password_refreshes_enc(client, db, no_spawn):
    db.add(_mk("nd-rd-3"))
    db.commit()
    resp = client.post("/api/v1/assets/nd-rd-3/agent/deploy", json={"password": "new-pass", "lang": "go"})
    assert resp.status_code == 200
    assert no_spawn["password"] == "new-pass"
    assert decrypt_secret(db.get(Asset, "nd-rd-3").ssh_password_enc) == "new-pass"  # 新密码已刷新落库


def test_redeploy_mother_maps_to_self_child(client, db, no_spawn):
    db.add(_mk("nd-rd-sc", ip="10.0.0.9"))
    db.add(_mk("mother-rd", ip="", kind="mother", self_child="nd-rd-sc"))
    db.commit()
    resp = client.post("/api/v1/assets/mother-rd/agent/deploy", json={})
    assert resp.status_code == 200
    assert no_spawn["asset_id"] == "nd-rd-sc"  # 部署目标自动落到本机子机
    assert no_spawn["password"] == "old-pass"  # 密码取本机子机落库值


def test_redeploy_rejects_unknown_no_ip_no_password(client, db, no_spawn):
    assert client.post("/api/v1/assets/no-such/agent/deploy", json={}).status_code == 404

    db.add(_mk("nd-rd-4", ip=""))
    db.commit()
    resp = client.post("/api/v1/assets/nd-rd-4/agent/deploy", json={})
    assert resp.status_code == 400 and "IP" in resp.json()["detail"]

    db.add(_mk("nd-rd-5", pwd=""))
    db.commit()
    resp = client.post("/api/v1/assets/nd-rd-5/agent/deploy", json={})
    assert resp.status_code == 400 and "SSH 密码" in resp.json()["detail"]

    db.add(_mk("mother-rd-2", ip="", kind="mother", self_child=""))
    db.commit()
    resp = client.post("/api/v1/assets/mother-rd-2/agent/deploy", json={})
    assert resp.status_code == 400 and "纳管" in resp.json()["detail"]
