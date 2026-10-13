"""资产 SSH 密码加密落库 + SSH 终端凭据链路。

覆盖：
- 新增子机（纳管）/ 新增母机（本机子机）时密码 Fernet 加密落库；
- 编辑资产三态更新（不传=不改 / 非空=更新 / 空串=清除）；
- SSH 终端凭据优先级（请求密码 > 保存密码 > 免密私钥），无凭据引导文案；
- 认证失败不限制重试次数（不锁定）；
- 删除子机卸载 Agent 时保存密码兜底；
- 历史数据兼容（无密码资产回退原通道）。
"""
from __future__ import annotations

import paramiko
import pytest

from app.models import Asset
from app.security import decrypt_secret, encrypt_secret


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_mod():
    from app import api

    return api


def _add_child(db: Asset, **kw) -> Asset:
    data = dict(
        id="node-10-20-0-1-22",
        hostname="10.20.0.1",
        kind="child",
        app="",
        role="other",
        env="prod",
        owner="",
        mother_id="mother-10-20-0-1",
        tenant_id="t1",
        extra={"provision": {"ip": "10.20.0.1", "port": 22, "username": "root", "status": "registered"}},
    )
    data.update(kw)
    a = Asset(**data)
    db.add(a)
    db.commit()
    return a


class _Stdout:
    def read(self) -> bytes:
        return (
            f"\n@@DVP_CWD@@/root\n@@DVP_HOME@@/root\n@@DVP_EC@@0\n"
        ).encode()


class _FakeSSH:
    """捕获 connect kwargs 的 paramiko 替身。"""

    last_connect: dict = {}

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, ip, **kw):
        _FakeSSH.last_connect = {"ip": ip, **kw}

    def exec_command(self, cmd, timeout=None):
        return None, _Stdout(), None

    def close(self):
        pass


# ===== 落库与编辑 =====


def test_provision_saves_encrypted_password(auth_token, client, db, api_mod, monkeypatch):
    """新增子机（一键纳管）：密码加密落库，接口/台账不回显明文。"""
    monkeypatch.setattr(api_mod, "_spawn_agent_deploy", lambda aid, **kw: None)
    r = client.post(
        "/api/v1/assets/provision",
        json={
            "ip": "10.20.0.9",
            "port": 22,
            "username": "root",
            "password": "SshPass#123",
            "owner": "张三",
            "owner_notify": {
                "channels": ["sms", "email", "dingtalk"],
                "phone": "13800000000",
                "email": "zs@example.com",
                "dingtalk_webhook": "https://oapi.dingtalk.com/robot/send?access_token=x",
            },
        },
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    aid = r.json()["id"]
    row = db.get(Asset, aid)
    assert row.ssh_password_enc and row.ssh_password_enc != "SshPass#123"
    assert decrypt_secret(row.ssh_password_enc) == "SshPass#123"
    assert row.owner == "张三"

    # 负责人推送配置已按姓名 upsert 到 owner_contacts
    from app.models import OwnerContact

    contact = db.query(OwnerContact).filter(OwnerContact.name == "张三").first()
    assert contact is not None
    assert contact.channels == ["sms", "email", "dingtalk"]
    assert contact.phone == "13800000000"

    detail = client.get(f"/api/v1/assets/{aid}", headers=_h(auth_token)).json()
    assert detail["ssh_password_set"] is True
    assert "SshPass#123" not in r.text


def test_update_asset_ssh_password_three_states(auth_token, client, db):
    """编辑三态：不传=不改；非空=更新；空串=清除。"""
    a = _add_child(db, ssh_password_enc=encrypt_secret("old-pass"))
    aid = a.id

    # 不传 ssh_password → 保持原值
    r = client.patch(f"/api/v1/assets/{aid}", json={"owner": "张三"}, headers=_h(auth_token))
    assert r.status_code == 200
    db.expire_all()
    assert decrypt_secret(db.get(Asset, aid).ssh_password_enc) == "old-pass"

    # 传新密码 → 更新
    r = client.patch(f"/api/v1/assets/{aid}", json={"ssh_password": "new-pass"}, headers=_h(auth_token))
    assert r.status_code == 200
    db.expire_all()
    assert decrypt_secret(db.get(Asset, aid).ssh_password_enc) == "new-pass"

    # 空串 → 清除
    r = client.patch(f"/api/v1/assets/{aid}", json={"ssh_password": ""}, headers=_h(auth_token))
    assert r.status_code == 200
    db.expire_all()
    assert db.get(Asset, aid).ssh_password_enc == ""
    assert db.get(Asset, aid).extra == a.extra  # 其他字段不受影响


def test_create_mother_saves_password_for_mother_and_self_child(
    auth_token, client, db, api_mod, monkeypatch
):
    """新增母机：母机与其唯一绑定的本机子机都保存密码（SSH 终端开箱即用）。"""
    monkeypatch.setattr(api_mod, "_verify_ssh", lambda ip, port, user, pwd: 88)
    monkeypatch.setattr(api_mod, "_spawn_agent_deploy", lambda aid, **kw: None)
    r = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-m-pw", "ip": "10.20.0.10", "ssh_port": 22, "username": "root", "password": "Mother#1"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    db.expire_all()
    mother = db.get(Asset, "mother-10-20-0-10")
    child = db.get(Asset, "node-10-20-0-10-22")
    assert decrypt_secret(mother.ssh_password_enc) == "Mother#1"
    assert child is not None and child.mother_id == mother.id  # 唯一绑定
    assert decrypt_secret(child.ssh_password_enc) == "Mother#1"


# ===== SSH 终端凭据链路 =====


def test_exec_uses_saved_password(auth_token, client, db, monkeypatch):
    """保存密码的子机：终端免输入密码即连（凭据优先级：请求密码 > 保存密码）。"""
    _add_child(db, ssh_password_enc=encrypt_secret("saved-pw"))
    monkeypatch.setattr(paramiko, "SSHClient", _FakeSSH)

    r = client.post(
        "/api/v1/assets/node-10-20-0-1-22/exec",
        json={"command": "uptime", "cwd": "~"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert _FakeSSH.last_connect["password"] == "saved-pw"
    assert _FakeSSH.last_connect["username"] == "root"

    # 请求显式带密码时优先用请求密码（API 兼容）
    r = client.post(
        "/api/v1/assets/node-10-20-0-1-22/exec",
        json={"command": "uptime", "cwd": "~", "password": "temp-pw"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert _FakeSSH.last_connect["password"] == "temp-pw"


def test_exec_without_credentials_guides_to_edit(auth_token, client, db, api_mod, monkeypatch):
    """历史资产（无保存密码）+ 私钥不可用 → 400 引导到编辑入口，而不是笼统失败。"""
    _add_child(db)  # 不带 ssh_password_enc（历史数据兼容形态）
    monkeypatch.setattr(api_mod, "_load_private_key_safe", lambda: None)

    r = client.post(
        "/api/v1/assets/node-10-20-0-1-22/exec",
        json={"command": "uptime", "cwd": "~"},
        headers=_h(auth_token),
    )
    assert r.status_code == 400
    assert "编辑资产" in r.json()["detail"]


def test_exec_saved_password_auth_fail_not_locked(auth_token, client, db, monkeypatch):
    """保存密码错误 → 409 引导更新；连续多次失败不锁定（无次数限制）。"""

    class _AuthFail(_FakeSSH):
        def connect(self, ip, **kw):
            raise paramiko.AuthenticationException()

    _add_child(db, ssh_password_enc=encrypt_secret("wrong-pw"))
    monkeypatch.setattr(paramiko, "SSHClient", _AuthFail)

    for _ in range(3):  # 不限制重试次数：反复失败也只返回同样 409
        r = client.post(
            "/api/v1/assets/node-10-20-0-1-22/exec",
            json={"command": "uptime", "cwd": "~"},
            headers=_h(auth_token),
        )
        assert r.status_code == 409
        assert "不限制重试次数" in r.json()["detail"]
        assert "编辑资产" in r.json()["detail"]


def test_exec_legacy_asset_falls_back_to_private_key(auth_token, client, db, api_mod, monkeypatch):
    """兼容历史：无保存密码但私钥可用 → 走免密私钥通道（原行为不变）。"""

    class _KeyOnly(_FakeSSH):
        pass

    _add_child(db)
    monkeypatch.setattr(api_mod, "_load_private_key_safe", lambda: object())
    monkeypatch.setattr(paramiko, "SSHClient", _KeyOnly)

    r = client.post(
        "/api/v1/assets/node-10-20-0-1-22/exec",
        json={"command": "uptime", "cwd": "~"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert _FakeSSH.last_connect["password"] is None  # 未传密码，纯私钥通道


# ===== 删除子机：保存密码兜底 =====


def test_remove_uninstall_uses_saved_password(auth_token, client, db, monkeypatch):
    """卸载 Agent 未传密码但有保存密码 → 自动回退，不再要求现场补填。"""
    from app.services import agent_deployer, provision as provision_svc

    _add_child(db, ssh_password_enc=encrypt_secret("saved-pw"))
    captured = {}

    def _fake_connect(ip, port, username, password):
        captured.update({"ip": ip, "port": port, "username": username, "password": password})
        return _FakeSSH()

    monkeypatch.setattr(provision_svc, "_connect_ssh", _fake_connect)
    monkeypatch.setattr(agent_deployer, "uninstall_via_ssh", lambda ssh, logs=None: logs.append("uninstalled"))

    r = client.post(
        "/api/v1/assets/node-10-20-0-1-22/remove",
        json={"uninstall": True, "ssh_password": ""},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert captured["password"] == "saved-pw"
    assert r.json()["uninstalled"] is True


def test_saved_password_roundtrip_with_special_chars():
    """加密助手往返：特殊字符密码可逆；空串语义为未配置。"""
    for pw in ("P@ss w0rd!", "中文密码123", 'quote"and\\slash'):
        enc = encrypt_secret(pw)
        assert enc != pw
        assert decrypt_secret(enc) == pw
    assert encrypt_secret("") == "" and decrypt_secret("") == ""
