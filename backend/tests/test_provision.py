"""资产页一键纳管：IP + SSH 密码 → 平台自动装 Zabbix Agent → 自动注册 → 回写 CMDB。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Asset, AuditLog
from app.services import provision as prov_mod

ROOT = Path(__file__).resolve().parents[2]


# ---------- Fake SSH（不出网） ----------


class FakeChan:
    """按 exec 顺序回放 (输出, 退出码) 序列的最小 channel 实现。"""

    def __init__(self, script: list[tuple[str, int]]):
        self.script = list(script)
        self.commands: list[str] = []
        self._sent = False

    def get_pty(self):
        pass

    def settimeout(self, t):
        pass

    def exec_command(self, cmd: str):
        self.commands.append(cmd)
        self._sent = False

    def recv_ready(self) -> bool:
        return not self._sent and bool(self.script and self.script[0][0])

    def recv(self, n: int) -> bytes:
        self._sent = True
        return self.script[0][0].encode()

    def exit_status_ready(self) -> bool:
        return self._sent

    def recv_exit_status(self) -> int:
        return self.script.pop(0)[1]

    def close(self):
        pass


class FakeTransport:
    def __init__(self, chan: FakeChan):
        self._chan = chan

    def open_session(self):
        return self._chan


class FakeSftp:
    def __init__(self):
        self.uploads: list[tuple[str, str]] = []

    def put(self, local: str, remote: str):
        assert Path(local).is_file(), "内置安装脚本不存在"
        self.uploads.append((local, remote))

    def chmod(self, remote: str, mode: int):
        assert mode == 0o755

    def close(self):
        pass


class FakeSsh:
    """一次 install_agent_via_ssh 调用 = 一个 channel，脚本按序回放。"""

    def __init__(self, script: list[tuple[str, int]]):
        self.chan = FakeChan(script)
        self.sftp = FakeSftp()

    def get_transport(self):
        return FakeTransport(self.chan)

    def open_sftp(self):
        return self.sftp

    def close(self):
        pass


# ---------- 测试 ----------


def test_connect_ssh_auth_failure_translated(monkeypatch):
    """SSH 认证失败 → 中文明确提示（不再裸抛英文 Authentication failed.）。"""
    import paramiko

    class FakeClient:
        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, *a, **kw):
            raise paramiko.AuthenticationException()

    monkeypatch.setattr(paramiko, "SSHClient", FakeClient)
    with pytest.raises(prov_mod.ProvisionError) as ei:
        prov_mod._connect_ssh("10.0.0.1", 22, "root", "bad")
    msg = str(ei.value)
    assert "用户名或密码错误" in msg and "10.0.0.1" in msg


def test_installer_script_synced_with_frontend_public():
    """backend 内置安装脚本必须与 frontend/public 下发版本一致，防止双份漂移。"""
    a = (ROOT / "backend/app/integrations/zabbix/install-zabbix-agent.sh").read_text()
    b = (ROOT / "frontend/public/install-zabbix-agent.sh").read_text()
    assert a.strip() == b.strip()


def test_asset_id_for_ip():
    assert prov_mod.asset_id_for_ip("10.0.0.55") == "node-10-0-0-55-22"
    assert prov_mod.asset_id_for_ip(" 10.1.2.3 ", 1001) == "node-10-1-2-3-1001"
    assert prov_mod.asset_id_for_ip("ranxx.cn", 1001) == "node-ranxx-cn-1001"
    # 同 IP 不同端口 → 不同资产
    assert prov_mod.asset_id_for_ip("10.0.0.55", 22) != prov_mod.asset_id_for_ip("10.0.0.55", 1001)


def test_resolve_zabbix_server(monkeypatch):
    assert prov_mod.resolve_zabbix_server(" 1.2.3.4 ") == "1.2.3.4"
    # 测试环境 ZABBIX_URL/PROVISION_ZABBIX_SERVER 均为空 → 空
    assert prov_mod.resolve_zabbix_server("") == ""
    from app.config import get_settings

    monkeypatch.setenv("PROVISION_ZABBIX_SERVER", "5.6.7.8")
    get_settings.cache_clear()
    try:
        assert prov_mod.resolve_zabbix_server("") == "5.6.7.8"
    finally:
        get_settings.cache_clear()


def test_provision_endpoint_creates_placeholder_and_dispatches(client, db, monkeypatch):
    """占位资产立刻可见（running）、后台任务被调度、密码不落库。"""
    called: dict = {}

    def fake_provision(asset_id, **kw):
        called["asset_id"] = asset_id
        called.update(kw)

    monkeypatch.setattr(prov_mod, "provision_node", fake_provision)

    resp = client.post(
        "/api/v1/assets/provision",
        json={"ip": "10.0.0.55", "password": "secret123", "zabbix_server": "124.221.251.186", "owner": "ops"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "node-10-0-0-55-22"

    import time

    for _ in range(20):  # 等后台线程把参数带给 fake
        if called:
            break
        time.sleep(0.05)
    assert called["asset_id"] == "node-10-0-0-55-22"
    assert called["ip"] == "10.0.0.55"
    assert called["zabbix_server"] == "124.221.251.186"

    a = db.get(Asset, "node-10-0-0-55-22")
    assert a is not None
    assert a.extra["provision"]["status"] == "running"
    assert "secret123" not in str(a.extra), "密码不允许写入资产记录"

    # 列表接口带纳管状态
    row = {x["id"]: x for x in client.get("/api/v1/assets").json()["items"]}["node-10-0-0-55-22"]
    assert row["provision_status"] == "running"


def test_provision_same_ip_different_port_creates_distinct_assets(client, db, monkeypatch):
    """同 IP 不同 SSH 端口是不同资产，互不覆盖。"""
    monkeypatch.setattr(prov_mod, "provision_node", lambda *a, **kw: None)
    for port in (22, 1001):
        resp = client.post(
            "/api/v1/assets/provision",
            json={"ip": "10.0.0.55", "port": port, "password": "x", "zabbix_server": "1.2.3.4"},
        )
        assert resp.status_code == 200
    ids = {a.id for a in db.query(Asset).filter(Asset.id.like("node-10-0-0-55%")).all()}
    assert ids == {"node-10-0-0-55-22", "node-10-0-0-55-1001"}


def test_provision_reuses_existing_asset_for_same_ip_port(client, db, monkeypatch):
    """同 (地址, 端口) 重复接入 → 复用已有资产（兼容旧格式 id），不新建卡片。"""
    monkeypatch.setattr(prov_mod, "provision_node", lambda *a, **kw: None)
    legacy = Asset(
        id="node-ranxx-cn",  # 旧格式 id（不含端口）
        hostname="ranxx.cn",
        app="web",
        role="app",
        env="prod",
        owner="ops",
        mother_id="",
        tenant_id="t1",
        reachable=True,
        extra={"provision": {"status": "installed", "ip": "ranxx.cn", "port": 1001}},
    )
    db.add(legacy)
    db.commit()

    resp = client.post(
        "/api/v1/assets/provision",
        json={"ip": "ranxx.cn", "port": 1001, "password": "x", "zabbix_server": "1.2.3.4"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "node-ranxx-cn", "复用旧资产 id，避免重复建卡"
    assert db.query(Asset).filter(Asset.id.like("node-ranxx-cn%")).count() == 1


def test_provision_endpoint_requires_config_or_explicit_server(client):
    resp = client.post("/api/v1/assets/provision", json={"ip": "10.0.0.99", "password": "x", "zabbix_server": ""})
    assert resp.status_code == 400
    assert "Zabbix Server" in resp.json()["detail"]


def test_provision_node_records_failure_and_audit(db, monkeypatch):
    """SSH 连不上 → status=failed、错误入 extra、审计留痕。"""

    def refused(*args, **kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(prov_mod, "_connect_ssh", refused)
    prov_mod.provision_node(
        "node-10-9-9-9",
        ip="10.9.9.9",
        port=22,
        username="root",
        password="pw",
        zabbix_server="124.221.251.186",
        requested_by="tester",
    )

    a = db.get(Asset, "node-10-9-9-9")
    assert a is not None, "失败也要保留占位资产便于排查"
    assert a.extra["provision"]["status"] == "failed"
    assert "connection refused" in a.extra["provision"]["error"]
    assert a.extra["provision"]["finished_at"]

    audit = db.query(AuditLog).filter(AuditLog.event_type == "asset_provision").all()
    assert any(r.result["status"] == "failed" and r.result["ip"] == "10.9.9.9" for r in audit)


def test_install_agent_via_ssh_as_root():
    """root 账号：直接 bash 执行，密码不进命令行。"""
    ssh = FakeSsh(
        [
            ("ROOT\n", 0),  # _detect_sudo
            ("==> 安装完成", 0),  # 安装脚本
            ("web-01\n", 0),  # hostname
        ]
    )
    logs: list[str] = []
    hostname = prov_mod.install_agent_via_ssh(
        ssh, password="pw", zabbix_server="124.221.251.186", logs=logs
    )
    assert hostname == "web-01"
    assert ssh.chan.commands[1] == "bash /tmp/install-zabbix-agent-devops.sh 124.221.251.186 devops-auto 120"
    assert "pw" not in ssh.chan.commands[1]
    assert ssh.sftp.uploads and ssh.sftp.uploads[0][1] == "/tmp/install-zabbix-agent-devops.sh"


def test_install_agent_via_ssh_with_sudo():
    """非 root 有 sudo：sudo -S 从 stdin 读密码（herestring），不回显。"""
    ssh = FakeSsh(
        [
            ("SUDO\n", 0),
            ("ok", 0),
            ("db-01\n", 0),
        ]
    )
    logs: list[str] = []
    hostname = prov_mod.install_agent_via_ssh(
        ssh, password="s3cret", zabbix_server="1.2.3.4", logs=logs
    )
    assert hostname == "db-01"
    cmd = ssh.chan.commands[1]
    assert cmd.startswith("sudo -S -p '' bash /tmp/install-zabbix-agent-devops.sh 1.2.3.4 devops-auto")
    assert "s3cret" in cmd  # 密码在 heredoc 里传给 sudo stdin
    assert not any("s3cret" in ln for ln in logs), "日志不得出现密码"


def test_install_agent_via_ssh_no_root_no_sudo():
    ssh = FakeSsh([("NOROOT\n", 0)])
    with pytest.raises(prov_mod.ProvisionError, match="sudo"):
        prov_mod.install_agent_via_ssh(ssh, password="pw", zabbix_server="1.2.3.4", logs=[])


def test_install_agent_via_ssh_custom_refresh():
    """自定义上报间隔（RefreshActiveChecks）透传到安装脚本第 3 个参数。"""
    ssh = FakeSsh(
        [
            ("ROOT\n", 0),
            ("==> 安装完成", 0),
            ("web-30\n", 0),
        ]
    )
    logs: list[str] = []
    hostname = prov_mod.install_agent_via_ssh(
        ssh, password="pw", zabbix_server="1.2.3.4", refresh_seconds=30, logs=logs
    )
    assert hostname == "web-30"
    assert ssh.chan.commands[1].endswith("bash /tmp/install-zabbix-agent-devops.sh 1.2.3.4 devops-auto 30")


def test_provision_node_full_success_flow(db, monkeypatch):
    """端到端（mock SSH）：装机 → CMDB 回写 hostname → 注册 hostid → status=registered。"""

    def fake_connect(ip, port, username, password):
        assert (ip, port, username) == ("10.2.3.4", 22, "root")
        return FakeSsh(
            [
                ("ROOT\n", 0),
                ("==> 安装完成", 0),
                ("web-42\n", 0),
            ]
        )

    monkeypatch.setattr(prov_mod, "_connect_ssh", fake_connect)
    monkeypatch.setattr(prov_mod, "wait_registered", lambda host, logs: "10086")

    prov_mod.provision_node(
        "node-10-2-3-4",
        ip="10.2.3.4",
        port=22,
        username="root",
        password="pw",
        zabbix_server="124.221.251.186",
        requested_by="tester",
    )

    a = db.get(Asset, "node-10-2-3-4")
    assert a.extra["provision"]["status"] == "registered"
    assert a.hostname == "web-42"
    assert a.zabbix_host == "web-42"
    assert a.external_id == "10086"
    assert a.reachable is True

    audit = db.query(AuditLog).filter(AuditLog.event_type == "asset_provision").all()
    assert any(r.result["status"] == "registered" for r in audit)


# ---------- 卸载 / 安装详情 / 采集提速 / 删除联动 ----------


def test_uninstall_agent_via_ssh_as_root():
    ssh = FakeSsh(
        [
            ("ROOT\n", 0),  # _detect_sudo
            ("GONE\n", 0),  # 卸载脚本输出
        ]
    )
    logs: list[str] = []
    prov_mod.uninstall_agent_via_ssh(ssh, password="pw", logs=logs)
    assert "GONE" in ssh.chan.commands[1]
    assert any("远端卸载完成" in ln for ln in logs)


def test_uninstall_agent_via_ssh_reports_leftover():
    """卸载后仍检出二进制/进程 → 报错而不是静默成功。"""
    ssh = FakeSsh([("ROOT\n", 0), ("LEFT\n", 0)])
    with pytest.raises(prov_mod.ProvisionError, match="残留"):
        prov_mod.uninstall_agent_via_ssh(ssh, password="pw", logs=[])


def test_uninstall_agent_via_ssh_retries_on_link_drop():
    """frp/NAT 链路中途断开（退出码 -1）→ 幂等重试一次后成功。"""
    ssh = FakeSsh([("ROOT\n", 0), ("packet_write_wait: broken pipe", -1), ("GONE\n", 0)])
    logs: list[str] = []
    prov_mod.uninstall_agent_via_ssh(ssh, password="pw", logs=logs)
    assert any("重试" in ln for ln in logs)
    assert any("远端卸载完成" in ln for ln in logs)


def test_uninstall_agent_via_ssh_fails_after_second_link_drop():
    """两次都被断链 → 明确提示链路问题与替代路径，而不是裸 -1。"""
    ssh = FakeSsh([("ROOT\n", 0), ("broken", -1), ("broken", -1)])
    with pytest.raises(prov_mod.ProvisionError, match="链路"):
        prov_mod.uninstall_agent_via_ssh(ssh, password="pw", logs=[])


def test_collect_install_info_parses_sections():
    out = (
        "@@VER@@\n5.0.47-1.el7\n"
        "@@BIN@@\n/usr/sbin/zabbix_agentd\n"
        "@@CONF@@\nServer=1.2.3.4\nServerActive=1.2.3.4:10051\nHostname=web-42\nHostMetadata=devops-auto\n"
        "@@LOG@@\n/var/log/zabbix/zabbix_agentd.log\n"
        "@@RUN@@\nsystemd:enabled/active\n"
        "@@PORT@@\nyes\n"
        "@@DONE@@\n"
    )
    ssh = FakeSsh([("ROOT\n", 0), (out, 0)])
    logs: list[str] = []
    info = prov_mod.collect_install_info(ssh, password="pw", logs=logs)
    assert info["version"] == "5.0.47-1.el7"
    assert info["binary"] == "/usr/sbin/zabbix_agentd"
    assert info["server"] == "1.2.3.4"
    assert info["server_active"] == "1.2.3.4:10051"
    assert info["hostname_in_conf"] == "web-42"
    assert info["run_mode"] == "systemd"
    assert info["run_state"] == "enabled/active"
    assert info["port_10050_listening"] is True


def test_speedup_host_items_updates_intervals():
    calls: list[tuple[str, object]] = []

    class FakeClient:
        def _rpc(self, method, params):
            calls.append((method, params))
            if method == "item.get":
                key = str(params["search"]["key_"])
                if key == "system.cpu.util":
                    return [{"itemid": "1", "key_": "system.cpu.util"}]
                if key == "vm.memory":
                    return [{"itemid": "2", "key_": "vm.memory.size[pavailable]"}]
                return []
            return "ok"

    logs: list[str] = []
    prov_mod.speedup_host_items(FakeClient(), "10086", logs)
    updates = [p for m, p in calls if m == "item.update"]
    assert len(updates) == 2
    assert all(u["update_interval"] == "30s" for u in updates)
    assert any("30s" in ln for ln in logs)


def test_remove_endpoint_deletes_child_without_uninstall(client, db):
    a = Asset(id="node-10-0-0-77", hostname="c-77", app="", role="app", env="prod", owner="", tenant_id="t1", reachable=True)
    db.add(a)
    db.commit()
    r = client.post("/api/v1/assets/node-10-0-0-77/remove", json={"uninstall": False})
    assert r.status_code == 200
    assert r.json()["deleted"] == "node-10-0-0-77"
    db.expire_all()  # 接口在另一个会话里提交删除，过期本会话缓存后再验证
    assert db.get(Asset, "node-10-0-0-77") is None


def test_remove_endpoint_uninstall_requires_source_and_password(client, db):
    """无纳管来源或未填 SSH 密码 → 明确 400，不做远程卸载。"""
    a = Asset(id="node-no-prov", hostname="np", app="", role="app", env="prod", owner="", tenant_id="t1", reachable=True)
    db.add(a)
    b = Asset(
        id="node-with-prov",
        hostname="wp",
        app="",
        role="app",
        env="prod",
        owner="",
        tenant_id="t1",
        reachable=True,
        extra={"provision": {"status": "registered", "ip": "10.0.0.9", "port": 22, "username": "root"}},
    )
    db.add(b)
    db.commit()

    r = client.post("/api/v1/assets/node-no-prov/remove", json={"uninstall": True, "ssh_password": "x"})
    assert r.status_code == 400
    assert "纳管来源" in r.json()["detail"]

    r = client.post("/api/v1/assets/node-with-prov/remove", json={"uninstall": True, "ssh_password": ""})
    assert r.status_code == 400
    assert "SSH 密码" in r.json()["detail"]


def test_remove_endpoint_with_uninstall_runs_remote_cleanup(client, db, monkeypatch):
    b = Asset(
        id="node-un-1",
        hostname="un-1",
        app="",
        role="app",
        env="prod",
        owner="",
        tenant_id="t1",
        reachable=True,
        extra={"provision": {"status": "registered", "ip": "10.0.0.9", "port": 22, "username": "root"}},
    )
    db.add(b)
    db.commit()
    monkeypatch.setattr(prov_mod, "_connect_ssh", lambda *a, **kw: FakeSsh([("ROOT\n", 0), ("GONE\n", 0)]))
    r = client.post("/api/v1/assets/node-un-1/remove", json={"uninstall": True, "ssh_password": "pw"})
    assert r.status_code == 200
    assert r.json()["uninstalled"] is True
    db.expire_all()
    assert db.get(Asset, "node-un-1") is None

    audit = db.query(AuditLog).filter(AuditLog.event_type == "asset_remove").all()
    assert any(x.result["asset_id"] == "node-un-1" and x.result["uninstalled"] for x in audit)


def test_remove_endpoint_mother_rules(client, db):
    """母机删除规则：勾卸载 → 400（走专用卸载接口）；仅删记录 → 允许（未部署母机必须能删）。"""
    m = Asset(
        id="m-x",
        hostname="mx",
        kind="mother",
        app="",
        role="app",
        env="prod",
        owner="",
        tenant_id="t1",
        reachable=True,
    )
    db.add(m)
    db.commit()

    # 勾选卸载 → 拒绝：母机卸载必须走 /mothers/{id}/uninstall（含 Zabbix 栈清理）
    r = client.post("/api/v1/assets/m-x/remove", json={"uninstall": True, "ssh_password": "pw"})
    assert r.status_code == 400

    # 仅删除记录 → 允许（部署失败的母机服务器上没有 Zabbix 栈，必须能直接删）
    r2 = client.post("/api/v1/assets/m-x/remove", json={})
    assert r2.status_code == 200
    db.expire_all()
    assert db.get(Asset, "m-x") is None


def test_get_asset_provision_returns_logs_and_install_info(client, db):
    a = Asset(
        id="node-pi-1",
        hostname="pi-1",
        app="",
        role="app",
        env="prod",
        owner="",
        tenant_id="t1",
        reachable=True,
        extra={
            "provision": {
                "status": "registered",
                "ip": "10.0.0.8",
                "port": 22,
                "username": "root",
                "logs": ["[1/4] 连接", "安装完成"],
                "install_info": {"version": "5.0.47-1", "run_mode": "systemd"},
            }
        },
    )
    db.add(a)
    db.commit()
    r = client.get("/api/v1/assets/node-pi-1/provision")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "registered"
    assert data["install_info"]["version"] == "5.0.47-1"
    assert data["logs"] == ["[1/4] 连接", "安装完成"]


# ---------- 子机命名 / 同 IP 不同端口唯一性（存量兼容） ----------


def test_provision_with_display_name_uses_it_and_keeps_zabbix_host(client, db, monkeypatch):
    """填了子机名称：资产展示名用名称，Zabbix 侧仍以真实主机名注册。"""
    # 第一步：拦截后台线程，验证接口把 display_name 传给安装流程
    captured = {}
    real_provision = prov_mod.provision_node

    def fake_provision(asset_id, **kw):
        captured["asset_id"] = asset_id
        captured.update(kw)

    monkeypatch.setattr(prov_mod, "provision_node", fake_provision)

    r = client.post(
        "/api/v1/assets/provision",
        json={
            "display_name": "订单-网关-01",
            "ip": "10.9.9.9",
            "port": 22,
            "username": "root",
            "password": "pw",
            "zabbix_server": "1.2.3.4",
            "app": "订单系统",
        },
    )
    assert r.status_code == 200
    aid = "node-10-9-9-9-22"
    assert captured["asset_id"] == aid
    assert captured["display_name"] == "订单-网关-01"

    # 第二步：恢复真实安装流程单线程执行（避免与后台线程争抢 FakeSsh 回放序列）
    monkeypatch.setattr(prov_mod, "provision_node", real_provision)
    ssh = FakeSsh(
        [
            ("ROOT\n", 0),                     # 安装前 _detect_sudo
            ("==> 安装完成", 0),                # 安装脚本
            ("web-42\n", 0),                   # hostname
            ("ROOT\n", 0),                     # 采集详情前 _detect_sudo
            ("@@VER@@\n6.0.1\n@@DONE@@\n", 0),  # 采集安装详情脚本
        ]
    )
    monkeypatch.setattr(prov_mod, "_connect_ssh", lambda *a, **kw: ssh)
    prov_mod.provision_node(
        aid,
        display_name="订单-网关-01",
        ip="10.9.9.9",
        port=22,
        username="root",
        password="pw",
        zabbix_server="1.2.3.4",
    )

    db.expire_all()
    a = db.get(Asset, aid)
    assert a.hostname == "订单-网关-01"          # 展示名 = 用户填的名称
    assert a.zabbix_host == "web-42"             # Zabbix 侧 = 真实主机名


def test_provision_display_name_duplicate_rejected(client, db):
    """子机名称与已有资产重名 → 409，不启动安装。"""
    db.add(Asset(id="dup-1", hostname="订单-网关-01", app="", role="app", env="prod", owner="", tenant_id="t1"))
    db.commit()
    r = client.post(
        "/api/v1/assets/provision",
        json={
            "display_name": "订单-网关-01",
            "ip": "10.9.9.10",
            "port": 22,
            "username": "root",
            "password": "pw",
            "zabbix_server": "1.2.3.4",
        },
    )
    assert r.status_code == 409


def test_provision_legacy_asset_without_port_record_new_port_creates_distinct(client, db):
    """存量资产（provision 无 port 字段，视为 22）与新端口部署互不影响：必须新建子机。"""
    db.add(
        Asset(
            id="node-10-0-0-5",
            hostname="10.0.0.5",
            app="",
            role="app",
            env="prod",
            owner="",
            tenant_id="t1",
            extra={"provision": {"status": "registered", "ip": "10.0.0.5"}},  # 旧格式：无 port
        )
    )
    db.commit()

    # 同 IP 但端口 1001 → 不能复用旧资产（尽管旧记录端口缺失）
    r = client.post(
        "/api/v1/assets/provision",
        json={"ip": "10.0.0.5", "port": 1001, "username": "root", "password": "pw", "zabbix_server": "1.2.3.4"},
    )
    assert r.status_code == 200
    assert r.json()["id"] == "node-10-0-0-5-1001"
    assert db.get(Asset, "node-10-0-0-5-1001") is not None
    assert db.get(Asset, "node-10-0-0-5") is not None  # 旧资产保持不动
