"""异常条目（仅 异常/恢复 两态）：webhook 幂等更新 + 列表接口 + 统计/母机归属。"""
from app.models import Asset
from tests.conftest import auth_headers

PROBLEM = {
    "EVENT.ID": "9001",
    "EVENT.VALUE": "1",
    "EVENT.NAME": "High CPU utilization (over 90% for 5m)",
    "TRIGGER.NAME": "High CPU utilization (over 90% for 5m)",
    "EVENT.SEVERITY": "High",
    "EVENT.NSEVERITY": "4",
    "HOST.NAME": "Zabbix server",
    "HOST.HOST": "Zabbix server",
    "HOST.ID": "10084",
    "HOST.IP": "124.221.251.186",
}


def _disable_auto_ticket(monkeypatch):
    monkeypatch.setenv("WEBHOOK_AUTO_TICKET", "false")
    from app.config import get_settings

    get_settings.cache_clear()


def test_webhook_records_abnormal_then_recovered(client, monkeypatch):
    _disable_auto_ticket(monkeypatch)

    first = client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM), headers=auth_headers())
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "abnormal"
    assert body["duplicate"] is False
    assert body["ticket"] is None
    assert body["anomaly"]["trigger_name"].startswith("High CPU utilization")
    assert body["anomaly"]["hostid"] == "10084"

    rows = client.get("/api/v1/anomalies").json()["items"]
    assert len(rows) == 1
    assert rows[0]["status"] == "abnormal"
    assert rows[0]["recovered_at"] is None

    # 恢复：同一 event_id，EVENT.VALUE=0
    resolved = dict(PROBLEM, **{"EVENT.VALUE": "0", "EVENT.NAME": "High CPU utilization (over 90% for 5m)"})
    second = client.post("/api/v1/webhooks/zabbix", json=resolved, headers=auth_headers())
    assert second.status_code == 200
    body2 = second.json()
    assert body2["status"] == "recovered"
    assert body2["duplicate"] is True
    assert body2["anomaly"]["recovered_at"] is not None

    listed = client.get("/api/v1/anomalies").json()
    assert listed["abnormal_count"] == 0
    assert len(listed["items"]) == 1
    assert listed["items"][0]["status"] == "recovered"


def test_webhook_repeated_problem_keeps_single_row(client, monkeypatch):
    _disable_auto_ticket(monkeypatch)

    for _ in range(3):
        resp = client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM), headers=auth_headers())
        assert resp.status_code == 200
    listed = client.get("/api/v1/anomalies").json()
    assert len(listed["items"]) == 1
    assert listed["items"][0]["status"] == "abnormal"
    assert listed["abnormal_count"] == 1


def test_anomalies_filter_by_status(client, monkeypatch):
    _disable_auto_ticket(monkeypatch)

    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM), headers=auth_headers())
    other = client.post(
        "/api/v1/webhooks/zabbix",
        json=dict(PROBLEM, **{"EVENT.ID": "9002", "EVENT.NAME": "High memory utilization", "TRIGGER.NAME": "High memory utilization"}),
        headers=auth_headers(),
    )
    assert other.status_code == 200
    # 9001 恢复
    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM, **{"EVENT.VALUE": "0"}), headers=auth_headers())

    abnormal = client.get("/api/v1/anomalies", params={"status": "abnormal"}).json()
    assert [i["event_id"] for i in abnormal["items"]] == ["9002"]

    recovered = client.get("/api/v1/anomalies", params={"status": "recovered"}).json()
    assert [i["event_id"] for i in recovered["items"]] == ["9001"]


def _mk_asset(aid: str, *, kind: str = "child", mother_id: str = "", group: str = "", hostname: str | None = None) -> Asset:
    return Asset(
        id=aid,
        hostname=hostname or aid,
        app="演示App",
        role="app",
        env="prod",
        owner="张三",
        group=group,
        kind=kind,
        mother_id=mother_id,
        tenant_id="tenant-default",
        extra={"provision": {"ip": "127.0.0.1", "status": "registered"}},
    )


def test_anomalies_attach_mother_group_child(client, db, monkeypatch):
    _disable_auto_ticket(monkeypatch)
    db.add(_mk_asset("mother-186", kind="mother", hostname="186"))
    # 复用 seed 的 ast-zabbix-server（hostname="Zabbix server"，external_id=10084）作为子机
    child = db.get(Asset, "ast-zabbix-server")
    child.mother_id = "mother-186"
    child.group = "订单系统"
    db.commit()

    # HOST.ID=10084 → external_id 匹配到子机 ast-zabbix-server
    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM), headers=auth_headers())

    listed = client.get("/api/v1/anomalies").json()
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["mother_id"] == "mother-186"
    assert item["mother_name"] == "186"
    assert item["group"] == "订单系统"
    assert item["child"] == "Zabbix server"


def test_anomalies_mother_self_fallback(client, db, monkeypatch):
    """母机自身 agent（"Zabbix server" 主机）无对应子机记录时，归属到母机自身（本机·母机）。"""
    _disable_auto_ticket(monkeypatch)
    db.add(_mk_asset("mother-186", kind="mother", hostname="186"))
    z = db.get(Asset, "ast-zabbix-server")
    if z is not None:
        db.delete(z)
    db.commit()

    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM), headers=auth_headers())

    listed = client.get("/api/v1/anomalies").json()
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["mother_id"] == "mother-186"
    assert item["mother_name"] == "186"
    assert item["child"] == "186"
    assert item["is_mother_self"] is True


def test_anomalies_pagination(client, monkeypatch):
    _disable_auto_ticket(monkeypatch)
    for i in range(1, 6):
        client.post(
            "/api/v1/webhooks/zabbix",
            json=dict(PROBLEM, **{"EVENT.ID": f"9{i:03d}", "TRIGGER.NAME": f"t{i}", "EVENT.NAME": f"t{i}"}),
            headers=auth_headers(),
        )

    page1 = client.get("/api/v1/anomalies", params={"page": 1, "page_size": 2}).json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    page3 = client.get("/api/v1/anomalies", params={"page": 3, "page_size": 2}).json()
    assert len(page3["items"]) == 1


def test_anomalies_filter_by_asset_id(client, db, monkeypatch):
    """asset_id 精确过滤：只返回该资产名下的异常条目（资产监控详情用）。"""
    _disable_auto_ticket(monkeypatch)
    db.add(_mk_asset("mother-186", kind="mother", hostname="186"))
    child = db.get(Asset, "ast-zabbix-server")  # seed 资产，external_id=10084
    child.mother_id = "mother-186"
    other = _mk_asset("ast-other", hostname="other-host", mother_id="mother-186", group="测试分组1")
    other.external_id = "20084"
    db.add(other)
    db.commit()

    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM), headers=auth_headers())
    client.post(
        "/api/v1/webhooks/zabbix",
        json=dict(
            PROBLEM,
            **{
                "EVENT.ID": "9500",
                "TRIGGER.NAME": "t-other",
                "EVENT.NAME": "t-other",
                "HOST.ID": "20084",
                "HOST.NAME": "other-host",
                "HOST.HOST": "other-host",
                "HOST.IP": "10.0.0.5",
            },
        ),
        headers=auth_headers(),
    )

    mine = client.get("/api/v1/anomalies", params={"asset_id": "ast-zabbix-server"}).json()
    assert mine["total"] == 1
    assert [i["event_id"] for i in mine["items"]] == ["9001"]
    assert mine["items"][0]["child"] == "Zabbix server"

    theirs = client.get("/api/v1/anomalies", params={"asset_id": "ast-other"}).json()
    assert theirs["total"] == 1
    assert [i["event_id"] for i in theirs["items"]] == ["9500"]
    assert theirs["items"][0]["child"] == "other-host"


def test_anomalies_stats(client, monkeypatch):
    _disable_auto_ticket(monkeypatch)
    # 两条异常，一条恢复
    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM), headers=auth_headers())
    client.post(
        "/api/v1/webhooks/zabbix",
        json=dict(PROBLEM, **{"EVENT.ID": "9101", "EVENT.NAME": "m", "TRIGGER.NAME": "m", "EVENT.SEVERITY": "Average", "EVENT.NSEVERITY": "3"}),
        headers=auth_headers(),
    )
    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM, **{"EVENT.VALUE": "0"}), headers=auth_headers())

    stats = client.get("/api/v1/anomalies/stats").json()
    assert stats["current_abnormal"] == 1
    assert stats["today_abnormal"] == 2
    assert stats["month_abnormal"] == 2
    assert stats["today_recovered"] == 1
    assert stats["avg_recover_minutes"] >= 0
    assert {s["severity"] for s in stats["severity_dist"]} == {"average", "high"}
    assert len(stats["trend_7d"]) == 7


# ===== 详情 / 异常时刻进程快照 =====

import time

from app.models import AnomalyEvent
from app.services import diagnostics as diagnostics_svc


_SNAPSHOT_RAW = """
@@LOAD@@
 14:00:00 up 12 days,  3:21,  1 user,  load average: 2.50, 1.80, 1.20
@@NCPU@@
2
@@MEM@@
Mem:            3736        2854         154          58         727        619
Swap:          2047          98        1949
@@DISK@@
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda1        40G   12G   26G  32% /
@@PROCS@@
152
3
1
@@TOPCPU@@
 1234     1 root        320.5  12.3  5-08:00:01 stress-ng stress-ng --cpu 8 --timeout 600s
  800     1 root          0.3   1.2 12-03:21:00 nginx nginx: worker process
@@TOPMEM@@
  900   800 mysql        12.0  45.6 3-12:00:00 mysqld /usr/sbin/mysqld --daemonize
 1234     1 root          0.3  12.3 5-08:00:01 stress-ng stress-ng --cpu 8 --timeout 600s
@@DSTATE@@
  PID  PPID USER            STAT     ELAPSED COMMAND ARGS
  777     1 root           D      01:00:03 dd dd if=/dev/zero of=/data/f bs=1M
@@LISTEN@@
tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=800,fd=3))
@@USERS@@
root pts/0 2026-09-23 09:00 (1.2.3.4)
@@END@@
"""


class _SnapStream:
    def read(self) -> bytes:
        return _SNAPSHOT_RAW.encode()


class _SnapSSH:
    def set_missing_host_key_policy(self, policy) -> None:  # noqa: ARG002
        return None

    def connect(self, *args, **kwargs) -> None:  # noqa: ARG002
        return None

    def exec_command(self, cmd: str, timeout: int | None = None):  # noqa: ARG002
        return None, _SnapStream(), _SnapStream()

    def close(self) -> None:
        return None


def test_snapshot_parsers():
    """uptime / TOP 进程 / D 状态进程输出的解析。"""
    parsed = diagnostics_svc._parse_uptime(
        " 14:00:00 up 12 days,  3:21,  1 user,  load average: 2.50, 1.80, 1.20"
    )
    assert parsed["load1"] == 2.5 and parsed["load5"] == 1.8 and parsed["load15"] == 1.2
    assert parsed["uptime_text"] == "12 days,  3:21"
    assert parsed["users_logged"] == 1

    rows = diagnostics_svc._parse_top_ps(
        " 1234     1 root        320.5  12.3  5-08:00:01 stress-ng stress-ng --cpu 8 --timeout 600s\n"
        "bad line skipped\n"
    )
    assert rows == [
        {
            "pid": 1234,
            "ppid": 1,
            "user": "root",
            "cpu": 320.5,
            "mem": 12.3,
            "etime": "5-08:00:01",
            "comm": "stress-ng",
            "args": "stress-ng --cpu 8 --timeout 600s",
        }
    ]

    d = diagnostics_svc._parse_dstate(
        "  PID  PPID USER            STAT     ELAPSED COMMAND ARGS\n"
        "  777     1 root           D      01:00:03 dd dd if=/dev/zero of=/data/f\n"
    )
    assert d[0]["pid"] == 777 and d[0]["stat"] == "D" and d[0]["comm"] == "dd"


def test_snapshot_passes_both_key_and_password(monkeypatch):
    """回归：密码非空（如全局兜底密码）时也必须保留私钥通道。

    纳管子机密码即用即弃不落库，_resolve_target 会用全局 DIAG_SSH_PASSWORD 填充，
    旧逻辑"密码非空就丢私钥"导致公钥免密的子机必然 Authentication failed。
    """
    captured: dict = {}

    class _CapSSH:
        def set_missing_host_key_policy(self, policy):  # noqa: ARG002
            return None

        def connect(self, *args, **kwargs):
            captured.update(kwargs)

        def exec_command(self, cmd, timeout=None):  # noqa: ARG002
            return None, _SnapStream(), _SnapStream()

        def close(self):
            return None

    monkeypatch.setattr(diagnostics_svc.paramiko, "SSHClient", lambda: _CapSSH())
    monkeypatch.setattr(diagnostics_svc, "_load_private_key", lambda path: "FAKE-KEY")

    diagnostics_svc._ssh_snapshot("10.0.0.9", 1002, "root", "some-fallback-pwd", key_path="/app/keys/inspect_key")
    assert captured["pkey"] == "FAKE-KEY"
    assert captured["password"] == "some-fallback-pwd"


def test_collect_for_anomaly_writes_snapshot(client, db, monkeypatch):
    """SSH 采集成功后把结构化进程快照写入 anomaly_events.diagnostics。"""
    _disable_auto_ticket(monkeypatch)
    monkeypatch.setattr(diagnostics_svc.paramiko, "SSHClient", _SnapSSH)

    asset = _mk_asset("ast-snap", hostname="snap-host")
    db.add(asset)
    db.flush()
    anomaly = AnomalyEvent(event_id="9100", trigger_name="High CPU utilization", asset_id=asset.id)
    db.add(anomaly)
    db.commit()

    diagnostics_svc.collect_for_anomaly(anomaly.id)

    db.refresh(anomaly)
    assert anomaly.diag_status == "done"
    snap = anomaly.diagnostics
    assert snap["target"] == {"ip": "127.0.0.1", "username": "root", "asset": "snap-host"}
    assert snap["collected_at"]
    # 系统状态
    assert snap["system"]["ncpu"] == 2
    assert snap["system"]["load1"] == 2.5 and snap["system"]["load15"] == 1.2
    assert snap["system"]["uptime_text"] == "12 days,  3:21"
    # 内存
    assert snap["memory"]["mem"]["total_mb"] == 3736
    assert snap["memory"]["swap"]["used_mb"] == 98
    # 磁盘 / 进程统计
    assert snap["disks"][0]["mount"] == "/" and snap["disks"][0]["pct"] == 32.0
    assert snap["processes"] == {"total": 152, "running": 3, "zombie": 1}
    # TOP 进程（含 PID / 用户 / 占比 / 运行时长 / 完整命令行）
    assert snap["top_cpu"][0]["pid"] == 1234 and snap["top_cpu"][0]["cpu"] == 320.5
    assert snap["top_mem"][0]["comm"] == "mysqld"
    assert "--daemonize" in snap["top_mem"][0]["args"]
    assert snap["d_state"][0]["stat"] == "D"
    # 监听端口 / 登录会话
    assert snap["listening"][0]["process"].startswith("sshd")
    assert snap["users"][0].startswith("root")


def test_webhook_auto_triggers_snapshot(client, db, monkeypatch):
    """首次异常上报自动后台采集异常时刻快照（mock 掉真实 SSH）。"""
    _disable_auto_ticket(monkeypatch)
    called = []
    monkeypatch.setattr(diagnostics_svc, "collect_for_anomaly", lambda aid: called.append(aid))

    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM), headers=auth_headers())
    deadline = time.time() + 2
    while not called and time.time() < deadline:
        time.sleep(0.05)
    assert called, "webhook 应触发后台快照采集"

    # 恢复/重复上报不再触发
    called.clear()
    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM, **{"EVENT.VALUE": "0"}), headers=auth_headers())
    time.sleep(0.1)
    assert not called


def test_anomaly_detail_and_snapshot(client, db, monkeypatch):
    """详情接口返回全量字段 + 快照占位；snapshot 接口可手动触发采集。"""
    _disable_auto_ticket(monkeypatch)
    called = []
    monkeypatch.setattr(diagnostics_svc, "collect_for_anomaly", lambda aid: called.append(aid))

    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM), headers=auth_headers())
    aid = client.get("/api/v1/anomalies").json()["items"][0]["id"]

    detail = client.get(f"/api/v1/anomalies/{aid}").json()
    assert detail["event_id"] == "9001"
    assert detail["payload"]["EVENT.ID"] == "9001"
    assert detail["diag_status"] == "none"
    assert detail["diagnostics"] == {}
    # 上机诊断/排查指南已移除
    assert "category" not in detail
    assert "playbooks" not in detail

    resp = client.post(f"/api/v1/anomalies/{aid}/snapshot")
    assert resp.status_code == 200
    deadline = time.time() + 2
    while len(called) < 2 and time.time() < deadline:
        time.sleep(0.05)
    # webhook 自动触发 1 次 + 手动 snapshot 1 次
    assert called == [aid, aid]

    assert client.get("/api/v1/anomalies/99999").status_code == 404
    assert client.post("/api/v1/anomalies/99999/snapshot").status_code == 404


def test_anomaly_logs_keep_every_notification(client, monkeypatch):
    """每条 webhook 通知都留痕：异常/恢复的原始载荷独立保存，互不覆盖。"""
    _disable_auto_ticket(monkeypatch)
    called = []
    monkeypatch.setattr(diagnostics_svc, "collect_for_anomaly", lambda aid: called.append(aid))

    # 2 次异常通知 + 1 次恢复通知
    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM), headers=auth_headers())
    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM, **{"EVENT.ACK.STATUS": "yes"}), headers=auth_headers())
    client.post("/api/v1/webhooks/zabbix", json=dict(PROBLEM, **{"EVENT.VALUE": "0"}), headers=auth_headers())

    aid = client.get("/api/v1/anomalies").json()["items"][0]["id"]
    detail = client.get(f"/api/v1/anomalies/{aid}").json()

    logs = detail["logs"]
    assert len(logs) == 3
    actions = [lg["action"] for lg in logs]
    assert actions.count("problem") == 2
    assert actions.count("recovered") == 1
    # 每条都保留原始载荷（EVENT.VALUE 各自独立：2 条异常=1，恢复=0）
    values = sorted(lg["payload"]["EVENT.VALUE"] for lg in logs)
    assert values == ["0", "1", "1"]
    assert all(lg["payload"]["EVENT.ID"] == "9001" for lg in logs)
    assert all(lg["received_at"] for lg in logs)
