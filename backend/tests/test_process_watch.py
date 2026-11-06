"""进程监听：配置下发（agent config）+ Agent 帧合并展示 + 增删端点（全程无 SSH）。"""
from __future__ import annotations

import time

from app.models import Asset
from app.routers.agent_api import LATEST, _LATEST_LOCK, pw_version_of
from app.services import process_watch as svc


def _mk(aid: str, agent_token: str = "", pw: list | None = None) -> Asset:
    extra: dict = {}
    if agent_token:
        extra["agent_token"] = agent_token
    if pw is not None:
        extra["process_watch"] = pw
    return Asset(
        id=aid,
        hostname=aid,
        app="演示App",
        role="app",
        env="prod",
        owner="张三",
        tenant_id="tenant-default",
        extra=extra,
    )


# ---------- 服务层：入参收敛 ----------

def test_normalize_new_basic():
    entry, err = svc.normalize_new("external", "name", "", "nginx", "")
    assert err == ""
    assert entry == {"ptype": "external", "match": "name", "pid": "", "name": "nginx", "container": ""}

    entry, err = svc.normalize_new("tomcat", "pid", "1234", "", "")
    assert err == ""
    assert entry["match"] == "pid" and entry["pid"] == "1234"

    entry, err = svc.normalize_new("docker", "", "", "", "my-web")
    assert err == ""
    assert entry["match"] == "container" and entry["container"] == "my-web"

    entry, err = svc.normalize_new("docker", "pid", " 88 ", "", "")
    assert err == ""
    assert entry["match"] == "pid" and entry["pid"] == "88"


def test_normalize_new_invalid():
    _, err = svc.normalize_new("bad", "name", "", "x", "")
    assert "进程类型不合法" in err
    _, err = svc.normalize_new("external", "name", "", "", "")
    assert "程序名称" in err
    _, err = svc.normalize_new("docker", "", "", "", "")
    assert "容器名称/ID" in err
    _, err = svc.normalize_new("external", "pid", "12ab", "", "")
    assert "程序名称" in err


def test_normalize_new_port():
    # 合法端口：收敛为十进制字符串
    entry, err = svc.normalize_new("external", "port", "", "", "", " 8080 ")
    assert err == ""
    assert entry == {"ptype": "external", "match": "port", "pid": "", "name": "", "container": "", "port": "8080"}

    # 边界：0 / 65536 非法，1 / 65535 合法
    _, err = svc.normalize_new("tomcat", "port", "", "", "", "0")
    assert "监听端口" in err
    _, err = svc.normalize_new("tomcat", "port", "", "", "", "65536")
    assert "监听端口" in err
    entry, err = svc.normalize_new("tomcat", "port", "", "", "", "1")
    assert err == "" and entry["port"] == "1"
    entry, err = svc.normalize_new("tomcat", "port", "", "", "", "65535")
    assert err == "" and entry["port"] == "65535"

    # 前导零归一化
    entry, err = svc.normalize_new("external", "port", "", "", "", "0080")
    assert err == "" and entry["port"] == "80"

    # 非法端口且无其他定位信息 → 提示补全
    _, err = svc.normalize_new("external", "port", "", "", "", "abc")
    assert "监听端口" in err
    _, err = svc.normalize_new("docker", "port", "", "", "", "abc")
    assert "容器名称/ID" in err


# ---------- 服务层：配置 + 帧合并 ----------

def test_entry_item_pending_when_no_report():
    item = svc.entry_item({"id": "pw_1", "ptype": "external", "match": "name", "name": "nginx"}, None)
    assert item["status"] == "pending"
    assert item["status_text"] == "待采集"
    assert item["display_name"] == "nginx"
    assert item["ptype_label"] == "外部程序"


def test_entry_item_host_found():
    ts = time.time()
    report = {
        "found": True, "pid": "42", "comm": "nginx", "args": "nginx: master",
        "stat": "S", "elapsed": "01:02:03", "ports": ["80", "443", "bad", "80"],
    }
    item = svc.entry_item({"id": "pw_1", "ptype": "external", "match": "name", "name": "nginx"}, report, ts)
    assert item["status"] == "running" and item["status_text"] == "运行中"
    assert item["pid"] == "42" and item["ports"] == ["80", "443"]
    assert item["elapsed"] == "01:02:03"
    assert item["checked_at"]  # 帧时间已回填


def test_entry_item_not_found_and_docker_status():
    item = svc.entry_item(
        {"id": "pw_2", "ptype": "external", "match": "pid", "pid": "9"},
        {"found": False},
    )
    assert item["status"] == "not_found" and item["status_text"] == "未找到"

    item = svc.entry_item(
        {"id": "pw_3", "ptype": "docker", "match": "container", "container": "web"},
        {"found": True, "name": "web", "status_raw": "exited", "image": "nginx:1.25", "ports": ["8080"]},
    )
    assert item["status"] == "exited" and item["status_text"] == "已退出"
    assert item["image"] == "nginx:1.25" and item["ports"] == ["8080"]


def test_merge_items_matches_by_id():
    entries = [
        {"id": "pw_a", "ptype": "external", "match": "name", "name": "nginx"},
        {"id": "pw_b", "ptype": "docker", "match": "container", "container": "web"},
    ]
    frame = {
        "ts": time.time(),
        "process_watch": [
            {"id": "pw_a", "found": True, "pid": "7", "stat": "R", "ports": ["80"]},
            {"id": "pw_b", "found": False},
        ],
    }
    items = svc.merge_items(entries, frame)
    assert items[0]["status"] == "running" and items[0]["pid"] == "7"
    assert items[1]["status"] == "not_found"
    # 帧里没有 pw_c 的报告 → pending
    items = svc.merge_items(entries + [{"id": "pw_c", "ptype": "tomcat", "match": "name", "name": "tomcat"}], frame)
    assert items[2]["status"] == "pending"
    # 空帧 → 全部 pending
    items = svc.merge_items(entries, None)
    assert all(i["status"] == "pending" for i in items)


# ---------- API：添加 / 删除 / 批量状态 / 配置下发 ----------

def test_add_and_delete_watch_api(client, db):
    db.add(_mk("ast-pw-1"))
    db.commit()

    resp = client.post("/api/v1/assets/ast-pw-1/process-watch", json={
        "ptype": "external", "match": "name", "name": "nginx",
    })
    assert resp.status_code == 200
    item = resp.json()["item"]
    assert item["status"] == "pending" and item["status_text"] == "待采集"
    wid = item["id"]

    # 落库验证
    row = db.get(Asset, "ast-pw-1")
    watches = row.extra["process_watch"]
    assert len(watches) == 1 and watches[0]["id"] == wid and watches[0]["name"] == "nginx"

    # 重复添加 → 409
    resp = client.post("/api/v1/assets/ast-pw-1/process-watch", json={
        "ptype": "external", "match": "name", "name": "nginx",
    })
    assert resp.status_code == 409

    # 非法参数 → 400
    resp = client.post("/api/v1/assets/ast-pw-1/process-watch", json={"ptype": "external"})
    assert resp.status_code == 400
    resp = client.post("/api/v1/assets/ast-pw-1/process-watch", json={"ptype": "nope", "name": "x"})
    assert resp.status_code == 400

    # 删除 → 200；再删 → 404
    resp = client.delete(f"/api/v1/assets/ast-pw-1/process-watch/{wid}")
    assert resp.status_code == 200 and resp.json()["deleted"] == wid
    resp = client.delete(f"/api/v1/assets/ast-pw-1/process-watch/{wid}")
    assert resp.status_code == 404


def test_add_watch_by_port_api(client, db):
    db.add(_mk("ast-pw-port"))
    db.commit()

    # 按端口添加 → 落库 entry 含 match=port
    resp = client.post("/api/v1/assets/ast-pw-port/process-watch", json={
        "ptype": "external", "match": "port", "port": " 22 ",
    })
    assert resp.status_code == 200
    item = resp.json()["item"]
    assert item["match"] == "port" and item["config_port"] == "22" and item["display_name"] == "端口 22"

    stored = db.get(Asset, "ast-pw-port").extra["process_watch"]
    assert len(stored) == 1 and stored[0]["port"] == "22" and stored[0]["match"] == "port"

    # 同端口重复添加 → 409
    resp = client.post("/api/v1/assets/ast-pw-port/process-watch", json={
        "ptype": "external", "match": "port", "port": "22",
    })
    assert resp.status_code == 409

    # 非法端口 → 400
    resp = client.post("/api/v1/assets/ast-pw-port/process-watch", json={
        "ptype": "external", "match": "port", "port": "99999",
    })
    assert resp.status_code == 400


def test_status_reads_agent_latest_frame(client, db):
    entry = {"id": "pw_x", "ptype": "external", "match": "name", "name": "nginx"}
    db.add(_mk("ast-pw-2", pw=[entry]))
    db.add(_mk("ast-pw-3"))  # 无监听配置
    db.commit()

    # Agent 尚未上报：条目呈待采集态
    resp = client.post("/api/v1/assets/process-watch/status", json={"asset_ids": ["ast-pw-2", "ast-pw-3", "ast-none"]})
    assert resp.status_code == 200
    data = resp.json()["items"]
    assert data["ast-pw-2"]["items"][0]["status"] == "pending"
    assert data["ast-pw-3"]["items"] == []
    assert data["ast-none"]["items"] == []

    # 模拟 Agent 上报帧（帧入 LATEST 后 status 端点合并出运行态）
    frame = {
        "ts": time.time(),
        "cpu": 10.0,
        "process_watch": [{"id": "pw_x", "found": True, "pid": "5", "stat": "S", "ports": ["80"]}],
    }
    with _LATEST_LOCK:
        LATEST["ast-pw-2"] = frame
    try:
        resp = client.post("/api/v1/assets/process-watch/status", json={"asset_ids": ["ast-pw-2"]})
        item = resp.json()["items"]["ast-pw-2"]["items"][0]
        assert item["status"] == "running" and item["pid"] == "5" and item["ports"] == ["80"]
    finally:
        with _LATEST_LOCK:
            LATEST.pop("ast-pw-2", None)


def test_agent_config_injects_process_watch(client, db):
    pw = [
        {"id": "pw_a", "ptype": "external", "match": "name", "name": "nginx"},
        {"id": "pw_b", "ptype": "docker", "match": "container", "container": "web"},
    ]
    db.add(_mk("ast-pw-4", agent_token="tok-pw", pw=pw))
    db.commit()

    resp = client.get("/api/v1/agent/config", params={"asset_id": "ast-pw-4"}, headers={"X-Agent-Token": "tok-pw"})
    assert resp.status_code == 200
    cfg = resp.json()["config"]
    assert cfg["process_watch"] == pw

    # 无监听配置的资产不下发该字段
    db.add(_mk("ast-pw-5", agent_token="tok-pw2"))
    db.commit()
    resp = client.get("/api/v1/agent/config", params={"asset_id": "ast-pw-5"}, headers={"X-Agent-Token": "tok-pw2"})
    assert "process_watch" not in resp.json()["config"]


# ---------- 指纹热生效：上报/配置响应回带 pw_ver，变更后 agent 立即热拉 ----------

def test_pw_version_of_fingerprint():
    assert pw_version_of([]) == ""
    pw = [{"id": "pw_a", "ptype": "external", "match": "name", "name": "nginx"}]
    v1 = pw_version_of(pw)
    assert len(v1) == 12 and pw_version_of(list(reversed(pw))) == v1  # 与顺序无关
    assert pw_version_of([{**pw[0], "name": "redis"}]) != v1  # 内容变化 → 指纹变化


def test_report_and_config_carry_pw_ver(client, db):
    db.add(_mk("ast-pw-6", agent_token="tok-pw6"))
    db.commit()
    hdr = {"X-Agent-Token": "tok-pw6"}

    # 无监听配置：上报/配置响应 pw_ver 均为空
    resp = client.post("/api/v1/agent/report", headers=hdr, json={
        "asset_id": "ast-pw-6", "samples": [{"ts": time.time(), "cpu": 1.0}],
    })
    assert resp.status_code == 200 and resp.json()["pw_ver"] == ""
    resp = client.get("/api/v1/agent/config", params={"asset_id": "ast-pw-6"}, headers=hdr)
    assert resp.json()["pw_ver"] == ""

    # 添加监听后：两个响应都带新指纹，且与 process_watch 内容一致
    resp = client.post("/api/v1/assets/ast-pw-6/process-watch", json={
        "ptype": "external", "match": "name", "name": "nginx",
    })
    assert resp.status_code == 200
    stored = db.get(Asset, "ast-pw-6").extra["process_watch"]
    expect = pw_version_of(stored)
    assert len(stored) == 1 and stored[0]["name"] == "nginx" and stored[0]["ptype"] == "external"

    resp = client.post("/api/v1/agent/report", headers=hdr, json={
        "asset_id": "ast-pw-6", "samples": [{"ts": time.time(), "cpu": 2.0}],
    })
    assert resp.json()["pw_ver"] == expect
    resp = client.get("/api/v1/agent/config", params={"asset_id": "ast-pw-6"}, headers=hdr)
    assert resp.json()["pw_ver"] == expect
    assert resp.json()["config"]["process_watch"] == stored

    # 删除后指纹回到空
    wid = resp.json()["config"]["process_watch"][0]["id"]
    client.delete(f"/api/v1/assets/ast-pw-6/process-watch/{wid}")
    resp = client.post("/api/v1/agent/report", headers=hdr, json={
        "asset_id": "ast-pw-6", "samples": [{"ts": time.time(), "cpu": 3.0}],
    })
    assert resp.json()["pw_ver"] == ""
