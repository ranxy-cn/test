from __future__ import annotations


class _FakeProc:
    def __init__(self) -> None:
        self.terminated = False

    def is_alive(self) -> bool:
        return not self.terminated

    def terminate(self) -> None:
        self.terminated = True

    def start(self) -> None:
        return None

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


class _FakeMpCtx:
    def Process(self, target=None, args=(), daemon=False):  # noqa: ARG002
        return _FakeProc()


class _FakeMp:
    @staticmethod
    def get_context(name):  # noqa: ARG004
        return _FakeMpCtx()


def test_mem_status_disabled_by_default(client):
    resp = client.get("/api/v1/tools/mem-stress")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["running"] is False


def test_mem_start_rejected_when_disabled(client):
    resp = client.post("/api/v1/tools/mem-stress", json={"target_percent": 95})
    assert resp.status_code == 403
    assert "STRESS_TOOLS_ENABLED" in resp.json()["detail"]


def test_mem_start_with_body_then_stop(client, monkeypatch):
    _enable(monkeypatch)
    from app.services import stress

    monkeypatch.setattr(stress, "mp", _FakeMp())

    resp = client.post(
        "/api/v1/tools/mem-stress",
        json={"target_percent": 99, "duration_seconds": 600},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["target_percent"] == 95  # 99 被钳制到上限 95
    assert 0 < body["seconds_remaining"] <= 600

    resp = client.post("/api/v1/tools/mem-stress/stop")
    assert resp.status_code == 200
    assert resp.json()["stopped"] == 1
    assert client.get("/api/v1/tools/mem-stress").json()["running"] is False


# ---------------------------------------------------------------------------
# 远程压测（按子机）
# ---------------------------------------------------------------------------

class _FakeChannel:
    def recv_exit_status(self) -> int:
        return 0


class _FakeStream:
    def __init__(self, data: str = "") -> None:
        self._data = data
        self.channel = _FakeChannel()

    def read(self) -> bytes:
        return self._data.encode()


class _FakeSSH:
    """记录 exec_command 的命令；按关键字匹配返回预置 stdout。"""

    def __init__(self, outputs: dict[str, str] | None = None) -> None:
        self.commands: list[str] = []
        self._outputs = outputs or {}

    def set_missing_host_key_policy(self, policy) -> None:  # noqa: ARG002
        return None

    def connect(self, *args, **kwargs) -> None:  # noqa: ARG002
        return None

    def exec_command(self, cmd: str, timeout: int | None = None):  # noqa: ARG002
        self.commands.append(cmd)
        out = next((v for k, v in self._outputs.items() if k in cmd), "")
        return None, _FakeStream(out), _FakeStream()

    def close(self) -> None:
        return None


def _make_child_asset(db, extra: dict | None = None) -> str:
    from app.models import Asset

    asset = Asset(
        id="ast-stress-child-01",
        hostname="stress-child-01",
        app="订单系统",
        role="app",
        owner="张三",
        tenant_id="tenant-default",
        kind="child",
        extra=extra if extra is not None else {"provision": {"ip": "10.0.0.9", "username": "root", "password": "pwd"}},
    )
    db.add(asset)
    db.commit()
    return asset.id


def test_stress_targets_lists_assets(client, db):
    from app.services import stress

    stress._remote_state.clear()
    aid = _make_child_asset(db)
    resp = client.get("/api/v1/tools/stress-targets")
    assert resp.status_code == 200
    targets = {t["asset_id"]: t for t in resp.json()["targets"]}
    assert targets[aid]["ssh_ready"] is True
    assert targets[aid]["ip"] == "10.0.0.9"
    assert targets[aid]["kind"] == "child"
    # 演示资产未录入 SSH 信息 → 标记不可远程压测
    assert targets["ast-order-app-01"]["ssh_ready"] is False


def test_remote_cpu_start_rejected_without_ssh(client, monkeypatch):
    _enable(monkeypatch)
    from app.services import stress

    stress._remote_state.clear()
    resp = client.post(
        "/api/v1/tools/cpu-stress",
        json={"duration_seconds": 60, "asset_id": "ast-order-app-01"},
    )
    assert resp.status_code == 400
    assert "SSH" in resp.json()["detail"]


def test_remote_cpu_start_status_and_stop(client, db, monkeypatch):
    _enable(monkeypatch)
    from app.services import stress

    stress._remote_state.clear()
    aid = _make_child_asset(db)
    fake = _FakeSSH({"nproc": "4"})
    monkeypatch.setattr(stress, "_connect", lambda target: fake)

    resp = client.post("/api/v1/tools/cpu-stress", json={"duration_seconds": 60, "asset_id": aid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["cores"] == 4
    assert body["hostname"] == "stress-child-01"
    # 每核一条自旋进程 + 标记串
    spawn = next(c for c in fake.commands if stress._MARK_CPU in c)
    assert spawn.count("timeout 60") == 4
    assert "nohup" in spawn
    # 回归：spawn 必须是合法 shell 语法。
    # 历史静默失败："&;" 拼接、done 后跟裸标记符，都会让 sh -c 语法报错退出，
    # stderr 被 nohup 吞掉，平台却返回"启动成功"，子机上毫无反应。
    import subprocess

    syntax = subprocess.run(["bash", "-n", "-c", spawn], capture_output=True, text=True)
    assert syntax.returncode == 0, f"压测命令语法非法: {syntax.stderr}"

    items = client.get("/api/v1/tools/remote-stress").json()["items"]
    assert [i["asset_id"] for i in items] == [aid]
    assert items[0]["seconds_remaining"] > 0

    resp = client.post("/api/v1/tools/stress/stop", json={"asset_id": aid})
    assert resp.status_code == 200
    assert resp.json()["stopped"] is True
    # 停止命令用方括号技巧 pkill 标记进程
    assert any("pkill -f" in c and "cp[u]" in c for c in fake.commands)
    assert client.get("/api/v1/tools/remote-stress").json()["items"] == []


def test_connect_passes_both_key_and_password(client, monkeypatch):
    """_connect 必须同时携带巡检私钥与密码（先公钥后密码）。

    回归：全局 DIAG_SSH_PASSWORD 兜底会让纳管子机拿到无关密码，
    旧逻辑“密码为空才用私钥”被挤掉私钥通道 → Authentication failed。
    """
    import app.services.stress as stress

    captured: dict = {}

    class _FakeParamikoClient:
        def set_missing_host_key_policy(self, policy):  # noqa: ARG002
            return None

        def connect(self, ip, **kw):
            captured.update(kw)
            captured["ip"] = ip

        def close(self):
            return None

    monkeypatch.setattr(stress.paramiko, "SSHClient", lambda: _FakeParamikoClient())
    monkeypatch.setattr("app.services.inspector._load_private_key", lambda path: "FAKE-KEY")

    stress._connect({"ip": "10.0.0.9", "port": 1002, "username": "root", "password": "some-pwd", "key_path": "/app/keys/inspect_key"})
    assert captured["ip"] == "10.0.0.9"
    assert captured["pkey"] == "FAKE-KEY"
    assert captured["password"] == "some-pwd"

    # 密码为空时同样带私钥
    captured.clear()
    stress._connect({"ip": "10.0.0.9", "port": 1002, "username": "root", "password": "", "key_path": "/app/keys/inspect_key"})
    assert captured["pkey"] == "FAKE-KEY"
    assert captured["password"] is None


def test_remote_mem_start_computes_target_mb(client, db, monkeypatch):
    _enable(monkeypatch)
    from app.services import stress

    stress._remote_state.clear()
    aid = _make_child_asset(db)
    # MemTotal=4000000KB, MemAvailable=3600000KB → 目标 95% 可压约 3259MB
    fake = _FakeSSH({"MemTotal": "4000000\n3600000"})
    monkeypatch.setattr(stress, "_connect", lambda target: fake)

    resp = client.post(
        "/api/v1/tools/mem-stress",
        json={"target_percent": 95, "duration_seconds": 60, "asset_id": aid},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["target_percent"] == 95
    assert body["target_mb"] == 3259
    spawn = next(c for c in fake.commands if "count=3259" in c)
    assert "/dev/shm/" in spawn and "trap" in spawn

    client.post("/api/v1/tools/stress/stop", json={"asset_id": aid})


def test_remote_mem_start_rejected_when_no_headroom(client, db, monkeypatch):
    _enable(monkeypatch)
    from app.services import stress

    stress._remote_state.clear()
    aid = _make_child_asset(db)
    # MemTotal=1000000KB，MemAvailable=200000KB → 目标 95% 需要压的空间不足 64MB
    fake = _FakeSSH({"MemTotal": "1000000\n200000"})
    monkeypatch.setattr(stress, "_connect", lambda target: fake)

    resp = client.post(
        "/api/v1/tools/mem-stress",
        json={"target_percent": 95, "duration_seconds": 60, "asset_id": aid},
    )
    assert resp.status_code == 400
    assert "余量不足" in resp.json()["detail"]
