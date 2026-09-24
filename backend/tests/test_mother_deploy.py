"""二期自动部署：母机一键安装 Zabbix 栈（编排渲染 + 部署接口 + 状态轮询）。"""
from __future__ import annotations

import time

from app.models import Asset
from app.services.mother_deploy import (
    _sudo_wrap,
    configure_autoreg,
    render_stack_compose,
    render_stack_env,
)


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk_mother(aid: str, ip: str, db_mode: str = "bundled") -> Asset:
    return Asset(
        id=aid,
        hostname=aid,
        app="Zabbix Server",
        role="mother",
        env="prod",
        owner="",
        kind="mother",
        db_mode=db_mode,
        tenant_id="tenant-default",
        extra={"zabbix": {"web_port": 8081, "db": {"mode": db_mode}}},
    )


# ---------- 纯函数：编排渲染 ----------


def test_render_compose_bundled():
    text = render_stack_compose(db_mode="bundled", db_host="", db_port=3306, web_port=8081)
    assert "zabbix/zabbix-server-mysql" in text
    assert "mysql:5.7" in text  # bundled 自带数据库容器
    assert "zabbix-web-nginx-mysql" in text
    assert "zabbix/zabbix-agent" in text
    assert '"10051:10051"' in text
    assert '"8081:8080"' in text
    assert "DB_SERVER_HOST: mysql" in text
    assert "${MYSQL_PASSWORD}" in text


def test_render_compose_external():
    text = render_stack_compose(db_mode="external", db_host="10.0.0.8", db_port=3307, web_port=9090)
    assert "mysql:5.7" not in text  # external 不拉数据库容器
    assert "DB_SERVER_HOST: 10.0.0.8" in text
    assert "DB_SERVER_PORT: 3307" in text
    assert '"9090:8080"' in text
    # external 模式同样由 .env 提供账号口令
    assert "MYSQL_PASSWORD: ${MYSQL_PASSWORD}" in text


def test_render_env():
    env = render_stack_env("dbpw", "rootpw")
    assert "MYSQL_PASSWORD=dbpw" in env
    assert "MYSQL_ROOT_PASSWORD=rootpw" in env


# ---------- sudo 密码注入 ----------


def test_sudo_wrap_root_passthrough():
    assert _sudo_wrap("", "", "docker info") == "docker info"


def test_sudo_wrap_injects_password():
    cmd = _sudo_wrap("sudo", "s3cret", "cd /opt && docker compose up -d")
    assert cmd.startswith("sudo -S -p '' bash -c ")
    assert "<<'PW'" in cmd and "\ns3cret\nPW" in cmd  # 密码经 stdin 注入
    assert "cd /opt && docker compose up -d" in cmd  # 原命令完整保留（shlex.quote）


# ---------- autoreg 初始化（幂等 + 缺模板兜底） ----------


class _FakeZbx:
    def __init__(self, groups=None, templates=None, actions=None):
        self.groups = groups or []
        self.templates = templates or []
        self.actions = actions or []
        self.calls: list[tuple[str, dict]] = []

    def _rpc(self, method, params):
        self.calls.append((method, params))
        if method == "host.get":
            return []  # 无 "Zabbix server" 主机，跳过自监控接口修正
        if method == "hostinterface.update":
            return {"interfaceids": [params["interfaceid"]]}
        if method == "hostgroup.get":
            return self.groups
        if method == "hostgroup.create":
            return {"groupids": ["88"]}
        if method == "template.get":
            return self.templates
        if method == "action.get":
            return self.actions
        if method == "action.create":
            return {"actionids": ["9"]}
        raise AssertionError(f"unexpected {method}")


def test_configure_autoreg_creates_all():
    logs: list[str] = []
    fake = _FakeZbx(templates=[{"templateid": "77"}])
    configure_autoreg(fake, logs)
    created = [p for m, p in fake.calls if m == "action.create"]
    assert len(created) == 1
    act = created[0]
    assert act["eventsource"] == 2
    assert act["filter"]["conditions"][0]["value"] == "devops-auto"
    types = [op["operationtype"] for op in act["operations"]]
    assert 2 in types and 4 in types and 6 in types  # 加 host + 加组 + 挂模板
    assert any("hostgroup.create" == m for m, _ in fake.calls)


def test_configure_autoreg_idempotent_and_no_template():
    logs: list[str] = []
    fake = _FakeZbx(groups=[{"groupid": "5"}], actions=[{"actionid": "9"}])
    configure_autoreg(fake, logs)
    assert not [p for m, p in fake.calls if m == "action.create"]  # 已存在则跳过
    assert not [p for m, p in fake.calls if m == "hostgroup.create"]  # 组已存在

    logs2: list[str] = []
    fake2 = _FakeZbx()  # 无组无模板
    configure_autoreg(fake2, logs2)
    created = [p for m, p in fake2.calls if m == "action.create"]
    assert len(created) == 1
    assert all(op["operationtype"] != 6 for op in created[0]["operations"])  # 缺模板不挂
    assert any("未找到 Linux 模板" in ln for ln in logs2)


# ---------- 接口 ----------


def test_deploy_api_flow(auth_token, client, db, monkeypatch):
    from app.services import mother_deploy as md

    captured = {}
    monkeypatch.setattr(md, "deploy_mother", lambda **kw: captured.update(kw))

    db.add(_mk_mother("mother-10-1-1-1", "10.1.1.1"))
    db.commit()

    # 启动部署：立即返回 running，后台任务收到 SSH 参数
    r = client.post(
        "/api/v1/assets/mothers/mother-10-1-1-1/deploy",
        json={"ip": "10.1.1.1", "port": 22, "username": "root", "password": "pw"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "running"

    for _ in range(30):
        if captured:
            break
        time.sleep(0.05)
    assert captured.get("asset_id") == "mother-10-1-1-1"
    assert captured.get("username") == "root"

    # 执行中重复部署 → 409
    r2 = client.post(
        "/api/v1/assets/mothers/mother-10-1-1-1/deploy",
        json={"ip": "10.1.1.1", "password": "pw"},
        headers=_h(auth_token),
    )
    assert r2.status_code == 409

    # 状态查询
    r3 = client.get("/api/v1/assets/mothers/mother-10-1-1-1/deploy", headers=_h(auth_token))
    assert r3.status_code == 200
    assert r3.json()["status"] == "running"

    # 非母机 / 不存在 → 404
    r4 = client.post(
        "/api/v1/assets/mothers/no-such/deploy",
        json={"ip": "10.1.1.1", "password": "pw"},
        headers=_h(auth_token),
    )
    assert r4.status_code == 404


def test_deploy_requires_password(auth_token, client):
    r = client.post(
        "/api/v1/assets/mothers/no-such/deploy",
        json={"ip": "10.1.1.1", "password": ""},
        headers=_h(auth_token),
    )
    assert r.status_code == 422  # pydantic 必填校验先于 404


def test_create_mother_deploy_now_requires_ssh_password(auth_token, client, db):
    r = client.post(
        "/api/v1/assets/mothers",
        json={
            "hostname": "ops-zbx-05",
            "ip": "10.0.0.15",
            "deploy_now": True,
            "ssh_password": "",
            "ack_risk": True,
        },
        headers=_h(auth_token),
    )
    assert r.status_code == 400


def test_create_mother_deploy_now_starts_thread(auth_token, client, db, monkeypatch):
    from app.services import mother_deploy as md

    captured = {}
    monkeypatch.setattr(md, "deploy_mother", lambda **kw: captured.update(kw))

    r = client.post(
        "/api/v1/assets/mothers",
        json={
            "hostname": "ops-zbx-06",
            "ip": "10.0.0.16",
            "deploy_now": True,
            "ssh_username": "root",
            "ssh_password": "pw",
            "ack_risk": True,
        },
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    row = db.get(Asset, "mother-10-0-0-16")
    assert row.extra["deploy"]["status"] == "running"

    for _ in range(30):
        if captured:
            break
        time.sleep(0.05)
    assert captured.get("asset_id") == "mother-10-0-0-16"
    assert captured.get("password") == "pw"
