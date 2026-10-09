"""资产页一键纳管：IP + SSH 密码 → 平台下发自研 agent（py/go 双语言）→ 上报接入。

覆盖：纳管端点（占位资产/调度/校验）、agent 部署器（SSH mock 全流程）、
删除与远程卸载端点、纳管详情查询。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.models import Asset, AuditLog
from app.services import agent_deployer as dep_mod
from app.services import provision as prov_mod

ROOT = Path(__file__).resolve().parents[2]


# ---------- Fake SSH（不出网） ----------


class FakeChan:
    """按 exec 顺序回放 (输出, 退出码) 序列的最小 channel 实现（空输出也允许）。"""

    def __init__(self, script: list[tuple[str, int]]):
        self.script = list(script)
        self.commands: list[str] = []
        self._sent = False

    def get_pty(self):
        pass

    def set_combine_stderr(self, v):
        pass

    def settimeout(self, t):
        pass

    def exec_command(self, cmd: str):
        self.commands.append(cmd)
        self._sent = False

    def recv_ready(self) -> bool:
        return not self._sent and bool(self.script)

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

    def set_keepalive(self, interval: int):
        pass


class FakeRemoteFile:
    """接收 str/bytes 写入的内存文件（模拟 paramiko sftp file 对象）。"""

    def __init__(self):
        self.chunks: list[str | bytes] = []

    def write(self, data):
        self.chunks.append(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getvalue(self) -> str:
        return b"".join(c.encode() if isinstance(c, str) else c for c in self.chunks).decode()


class FakeSftp:
    """记录 sftp.put/rename/open 写入，供断言上传来源与配置内容。"""

    def __init__(self):
        self.uploads: list[tuple[str, str]] = []
        self.renames: list[tuple[str, str]] = []
        self.written: dict[str, FakeRemoteFile] = {}

    def put(self, local: str, remote: str):
        assert Path(local).is_file(), f"agent 源文件不存在：{local}"
        self.uploads.append((local, remote))

    def rename(self, a: str, b: str):
        self.renames.append((a, b))

    def posix_rename(self, a: str, b: str):
        self.renames.append((a, b))

    def remove(self, remote: str):
        pass

    def open(self, remote: str, mode: str = "r"):
        buf = FakeRemoteFile()
        self.written[remote] = buf
        return buf

    def close(self):
        pass


class FakeSsh:
    """一次远程调用 = 一个 channel，脚本按序回放。"""

    def __init__(self, script: list[tuple[str, int]]):
        self.chan = FakeChan(script)
        self.sftp = FakeSftp()

    def get_transport(self):
        return FakeTransport(self.chan)

    def open_sftp(self):
        return self.sftp

    def close(self):
        pass


# ---------- SSH 辅助 / ID 规则 ----------


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


def test_asset_id_for_ip():
    assert prov_mod.asset_id_for_ip("10.0.0.55") == "node-10-0-0-55-22"
    assert prov_mod.asset_id_for_ip(" 10.1.2.3 ", 1001) == "node-10-1-2-3-1001"
    assert prov_mod.asset_id_for_ip("ranxx.cn", 1001) == "node-ranxx-cn-1001"
    # 同 IP 不同端口 → 不同资产
    assert prov_mod.asset_id_for_ip("10.0.0.55", 22) != prov_mod.asset_id_for_ip("10.0.0.55", 1001)


# ---------- agent 部署器（agent_deployer） ----------


def _make_asset(db, aid: str) -> None:
    db.add(
        Asset(
            id=aid,
            hostname="10.5.5.5:22",
            kind="child",
            app="",
            role="app",
            env="prod",
            owner="",
            tenant_id="t1",
            reachable=False,
            extra={"provision": {"status": "running", "ip": "10.5.5.5", "port": 22}},
        )
    )
    db.commit()


def test_deploy_to_host_py_success(db, monkeypatch):
    """py 版部署全流程（mock SSH）：上传 agent+config → 拉起 → 首帧确认 success。"""
    from app.routers.agent_api import LATEST

    aid = "node-10-5-5-5"
    _make_asset(db, aid)
    monkeypatch.setitem(LATEST, aid, {"cpu_pct": 1.0, "ts": time.time() + 30})  # 新样本 ts > 部署开始 → 首帧确认立即通过

    ssh = FakeSsh(
        [
            ("ROOT\n", 0),  # _detect_sudo
            ("Python 3.11.9\n", 0),  # python3 检测
            ("", 0),  # mkdir -p 安装目录
            ("", 0),  # pkill 旧进程
            ("12345\n", 0),  # nohup 拉起，输出 pid
        ]
    )
    monkeypatch.setattr(dep_mod, "_connect_ssh", lambda *a, **kw: ssh)  # deployer 是 from-import
    dep_mod.deploy_to_host(
        db,
        aid,
        ip="10.5.5.5",
        lang="py",
        ssh_user="root",
        ssh_password="pw",
        token="tok-1",
        server_url="http://10.0.0.1:8000",
        cfg={"interval_seconds": 5},
        port=22,
        requested_by="tester",
    )

    a = db.get(Asset, aid)
    st = a.extra["agent_deploy"]
    assert st["state"] == "success"
    assert st["lang"] == "py" and st["pid"] == "12345" and st["requested_by"] == "tester"
    assert any("首帧上报确认" in s for s in st["steps"])

    # 上传：agent.py（put→rename 原子落位）与 config.json（含 server/token/asset_id）
    src_name, dst = dep_mod.ASSET_FILES["py"]
    assert ssh.sftp.uploads and ssh.sftp.uploads[0][0].endswith(src_name)
    assert ssh.sftp.renames == [(f"{dep_mod.AGENT_DIR}/{dst}.tmp", f"{dep_mod.AGENT_DIR}/{dst}")]
    cfg_data = json.loads(ssh.sftp.written[f"{dep_mod.AGENT_DIR}/config.json"].getvalue())
    assert cfg_data["server"] == "http://10.0.0.1:8000"
    assert cfg_data["token"] == "tok-1"
    assert cfg_data["asset_id"] == aid
    assert cfg_data["agent_config"] == {"interval_seconds": 5}


def test_deploy_to_host_py_without_python3_fails_clearly(db, monkeypatch):
    """目标机无 python3 且自动安装失败 → 报错提示改用 Go 版（零依赖）。"""
    aid = "node-10-5-5-6"
    _make_asset(db, aid)
    ssh = FakeSsh(
        [
            ("ROOT\n", 0),  # _detect_sudo
            ("", 127),  # python3 缺失
            ("", 0),  # command -v apt-get（有 apt）
            ("", 1),  # apt-get 安装 python3 失败
            ("", 127),  # 复验 python3 --version 仍缺失
        ]
    )
    monkeypatch.setattr(dep_mod, "_connect_ssh", lambda *a, **kw: ssh)  # deployer 是 from-import
    with pytest.raises(dep_mod.ProvisionError, match="Go 版"):
        dep_mod.deploy_to_host(
            db,
            aid,
            ip="10.5.5.5",
            lang="py",
            ssh_user="root",
            ssh_password="pw",
            token="t",
            server_url="http://s",
            cfg={},
            port=22,
        )


def test_uninstall_via_ssh_success():
    ssh = FakeSsh(
        [
            ("ROOT\n", 0),  # _detect_sudo
            ("", 0),  # pkill 旧进程
            ("REMOVED\n", 0),  # rm -rf 安装目录
        ]
    )
    logs: list[str] = []
    dep_mod.uninstall_via_ssh(ssh, logs=logs)
    assert "pkill -f 'devops-agent[/]agent'" in ssh.chan.commands[1]
    assert "pkill -f '[.]/agent --config'" in ssh.chan.commands[1]
    assert "rm -rf /opt/devops-agent" in ssh.chan.commands[2]
    assert any("已停止 agent 进程并清理 /opt/devops-agent" in ln for ln in logs)


def test_uninstall_via_ssh_failure_raises():
    ssh = FakeSsh([("ROOT\n", 0), ("", 0), ("permission denied\n", 1)])
    with pytest.raises(dep_mod.ProvisionError, match="清理安装目录失败"):
        dep_mod.uninstall_via_ssh(ssh, logs=[])


# ---------- 纳管端点（POST /assets/provision） ----------


def test_provision_endpoint_creates_placeholder_and_dispatches(client, db, monkeypatch):
    """占位资产立刻可见（running）、后台部署被调度、密码不落库、token 已签发。"""
    called: dict = {}

    def fake_deploy(db2, asset_id, **kw):
        called["asset_id"] = asset_id
        called.update(kw)

    monkeypatch.setattr(dep_mod, "deploy_to_host", fake_deploy)

    resp = client.post(
        "/api/v1/assets/provision",
        json={"display_name": "订单-网关-01", "ip": "10.0.0.55", "password": "secret123", "owner": "ops"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "node-10-0-0-55-22"
    assert body["status"] == "running"
    assert body["server_url"].startswith("http")
    assert "纳管" in body["message"]

    for _ in range(40):  # 等后台线程把参数带给 fake
        if called:
            break
        time.sleep(0.05)
    assert called["asset_id"] == "node-10-0-0-55-22"
    assert called["ip"] == "10.0.0.55"
    assert called["lang"] == "py"
    assert called["ssh_user"] == "root"
    assert called["ssh_password"] == "secret123"
    assert called["port"] == 22
    assert called["requested_by"]

    a = db.get(Asset, "node-10-0-0-55-22")
    assert a is not None
    assert a.hostname == "订单-网关-01"  # 展示名优先
    assert a.extra["provision"]["status"] == "running"
    assert a.extra["agent_token"], "必须签发 agent 上报令牌"
    assert called["token"] == a.extra["agent_token"]
    assert "secret123" not in str(a.extra), "密码不允许写入资产记录"

    # 列表接口带纳管状态
    row = {x["id"]: x for x in client.get("/api/v1/assets").json()["items"]}["node-10-0-0-55-22"]
    assert row["provision_status"] == "running"


def test_provision_lang_go_dispatch_and_invalid_rejected(client, monkeypatch):
    """lang=go 合法并透传调度；非法语言 422。"""
    called: dict = {}

    def fake_deploy(db2, asset_id, **kw):
        called["asset_id"] = asset_id
        called.update(kw)

    monkeypatch.setattr(dep_mod, "deploy_to_host", fake_deploy)
    r = client.post("/api/v1/assets/provision", json={"ip": "10.0.0.61", "password": "x", "lang": "go"})
    assert r.status_code == 200
    for _ in range(40):
        if called:
            break
        time.sleep(0.05)
    assert called["lang"] == "go"

    r2 = client.post("/api/v1/assets/provision", json={"ip": "10.0.0.62", "password": "x", "lang": "rust"})
    assert r2.status_code == 422


def test_provision_reuses_existing_asset_for_same_ip_port(client, db, monkeypatch):
    """同 (地址, 端口) 重复接入 → 复用已有资产（兼容旧格式 id），不新建卡片。"""
    monkeypatch.setattr(dep_mod, "deploy_to_host", lambda *a, **kw: None)
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
        extra={"provision": {"status": "success", "ip": "ranxx.cn", "port": 1001}},
    )
    db.add(legacy)
    db.commit()

    resp = client.post("/api/v1/assets/provision", json={"ip": "ranxx.cn", "port": 1001, "password": "x"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "node-ranxx-cn", "复用旧资产 id，避免重复建卡"
    assert db.query(Asset).filter(Asset.id.like("node-ranxx-cn%")).count() == 1
    db.expire_all()
    a = db.get(Asset, "node-ranxx-cn")
    assert a.extra["provision"]["status"] == "running"  # 重跑时状态重置
    assert a.extra["agent_token"]  # 并重新签发上报令牌


def test_provision_same_ip_different_port_creates_distinct_assets(client, db, monkeypatch):
    """同 IP 不同 SSH 端口是不同资产，互不覆盖。"""
    monkeypatch.setattr(dep_mod, "deploy_to_host", lambda *a, **kw: None)
    for port in (22, 1001):
        resp = client.post("/api/v1/assets/provision", json={"ip": "10.0.0.55", "port": port, "password": "x"})
        assert resp.status_code == 200
    ids = {a.id for a in db.query(Asset).filter(Asset.id.like("node-10-0-0-55%")).all()}
    assert ids == {"node-10-0-0-55-22", "node-10-0-0-55-1001"}


def test_provision_legacy_without_port_and_new_port_distinct(client, db, monkeypatch):
    """存量资产（provision 无 port 字段，视为 22）与新端口部署互不影响：必须新建子机。"""
    monkeypatch.setattr(dep_mod, "deploy_to_host", lambda *a, **kw: None)
    db.add(
        Asset(
            id="node-10-0-0-5",
            hostname="10.0.0.5",
            app="",
            role="app",
            env="prod",
            owner="",
            tenant_id="t1",
            extra={"provision": {"status": "success", "ip": "10.0.0.5"}},  # 旧格式：无 port
        )
    )
    db.commit()

    # 同 IP 但端口 1001 → 不能复用旧资产（尽管旧记录端口缺失）
    r = client.post("/api/v1/assets/provision", json={"ip": "10.0.0.5", "port": 1001, "password": "pw"})
    assert r.status_code == 200
    assert r.json()["id"] == "node-10-0-0-5-1001"
    assert db.get(Asset, "node-10-0-0-5-1001") is not None
    assert db.get(Asset, "node-10-0-0-5") is not None  # 旧资产保持不动


def test_provision_display_name_duplicate_rejected(client, db):
    """子机名称与已有资产重名 → 409，不启动安装。"""
    db.add(Asset(id="dup-1", hostname="订单-网关-01", app="", role="app", env="prod", owner="", tenant_id="t1"))
    db.commit()
    r = client.post(
        "/api/v1/assets/provision",
        json={"display_name": "订单-网关-01", "ip": "10.9.9.10", "password": "pw"},
    )
    assert r.status_code == 409


def test_provision_mother_validation(client, db, monkeypatch):
    """母机校验：不存在 404 / 非母机 400 / 母机自身 IP 允许纳管（本机子机）。"""
    monkeypatch.setattr(dep_mod, "deploy_to_host", lambda *a, **kw: None)
    db.add(
        Asset(
            id="m-1",
            hostname="mother-host",
            kind="mother",
            app="",
            role="app",
            env="prod",
            owner="",
            tenant_id="t1",
            extra={"provision": {"ip": "10.1.1.1", "port": 22}},
        )
    )
    db.add(Asset(id="child-1", hostname="c-1", app="", role="app", env="prod", owner="", tenant_id="t1"))
    db.commit()

    r = client.post("/api/v1/assets/provision", json={"ip": "10.9.9.1", "password": "x", "mother_id": "no-such"})
    assert r.status_code == 404
    assert "归属母机不存在" in r.json()["detail"]

    r = client.post("/api/v1/assets/provision", json={"ip": "10.9.9.2", "password": "x", "mother_id": "child-1"})
    assert r.status_code == 400
    assert "不是母机" in r.json()["detail"]

    r = client.post("/api/v1/assets/provision", json={"ip": "10.1.1.1", "password": "x", "mother_id": "m-1"})
    assert r.status_code == 200  # 母机本机子机：允许纳管
    assert r.json()["status"] == "running"


# ---------- 删除 / 卸载端点 ----------


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
        extra={"provision": {"status": "success", "ip": "10.0.0.9", "port": 22, "username": "root"}},
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
    """勾选卸载：SSH 连接 + 调用 agent 卸载器 + 删除记录 + 审计留痕。"""
    b = Asset(
        id="node-un-1",
        hostname="un-1",
        app="",
        role="app",
        env="prod",
        owner="",
        tenant_id="t1",
        reachable=True,
        extra={"provision": {"status": "success", "ip": "10.0.0.9", "port": 22, "username": "root"}},
    )
    db.add(b)
    db.commit()
    seen: dict = {}

    def fake_uninstall(ssh, logs=None):
        seen["ssh"] = ssh
        if logs is not None:
            logs.append("已停止 agent 进程并清理 /opt/devops-agent")

    monkeypatch.setattr(prov_mod, "_connect_ssh", lambda *a, **kw: FakeSsh([]))
    monkeypatch.setattr(dep_mod, "uninstall_via_ssh", fake_uninstall)
    r = client.post("/api/v1/assets/node-un-1/remove", json={"uninstall": True, "ssh_password": "pw"})
    assert r.status_code == 200
    assert r.json()["uninstalled"] is True
    assert isinstance(seen.get("ssh"), FakeSsh)
    assert any("清理" in ln for ln in r.json()["uninstall_logs"])
    db.expire_all()
    assert db.get(Asset, "node-un-1") is None

    audit = db.query(AuditLog).filter(AuditLog.event_type == "asset_remove").all()
    assert any(x.result["asset_id"] == "node-un-1" and x.result["uninstalled"] for x in audit)


def test_remove_endpoint_uninstall_failure_returns_502(client, db, monkeypatch):
    """远程卸载失败（如清理目录失败）→ 502 且资产保留，便于重试。"""
    b = Asset(
        id="node-un-2",
        hostname="un-2",
        app="",
        role="app",
        env="prod",
        owner="",
        tenant_id="t1",
        reachable=True,
        extra={"provision": {"status": "success", "ip": "10.0.0.10", "port": 22, "username": "root"}},
    )
    db.add(b)
    db.commit()

    def boom(ssh, logs=None):
        raise dep_mod.ProvisionError("清理安装目录失败：boom")

    monkeypatch.setattr(prov_mod, "_connect_ssh", lambda *a, **kw: FakeSsh([]))
    monkeypatch.setattr(dep_mod, "uninstall_via_ssh", boom)
    r = client.post("/api/v1/assets/node-un-2/remove", json={"uninstall": True, "ssh_password": "pw"})
    assert r.status_code == 502
    assert "远程卸载失败" in r.json()["detail"]
    assert db.get(Asset, "node-un-2") is not None, "卸载失败不应删除资产"


def test_remove_endpoint_mother_rules(client, db):
    """母机删除规则：勾卸载 → 400（纯台账，不支持卸载）；仅删记录（无子机）→ 允许。"""
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
    db.add(
        Asset(
            id="node-mx",
            hostname="node-mx",
            kind="node",
            app="",
            role="app",
            env="prod",
            owner="",
            mother_id="m-x",
            tenant_id="t1",
        )
    )
    db.commit()

    # 有子机 → 先删子机
    r = client.post("/api/v1/assets/m-x/remove", json={})
    assert r.status_code == 400
    assert "先删除" in r.json()["detail"]
    assert db.get(Asset, "m-x") is not None

    # 删净子机后：勾卸载 → 400（母机纯台账，不支持卸载）
    assert client.post("/api/v1/assets/node-mx/remove", json={"uninstall": False}).status_code == 200
    r1 = client.post("/api/v1/assets/m-x/remove", json={"uninstall": True, "ssh_password": "pw"})
    assert r1.status_code == 400
    assert "不支持卸载" in r1.json()["detail"]

    r2 = client.post("/api/v1/assets/m-x/remove", json={})
    assert r2.status_code == 200
    db.expire_all()
    assert db.get(Asset, "m-x") is None


# ---------- 纳管详情 ----------


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
                "status": "success",
                "ip": "10.0.0.8",
                "port": 22,
                "username": "root",
                "logs": ["[1/4] 连接", "已启动 pid=12345"],
                "install_info": {"lang": "py", "pid": "12345"},
            }
        },
    )
    db.add(a)
    db.commit()
    r = client.get("/api/v1/assets/node-pi-1/provision")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["install_info"] == {"lang": "py", "pid": "12345"}
    assert data["logs"] == ["[1/4] 连接", "已启动 pid=12345"]
