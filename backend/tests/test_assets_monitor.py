"""资产监控：定时探活 / 监控大盘 / SSH 实时巡检。"""
from __future__ import annotations

import socket
import threading

from app.models import Asset
from app.services.probe import run_probe_cycle


def _mk(aid: str, ip: str = "", port: int = 22) -> Asset:
    extra = {"provision": {"ip": ip, "port": port, "status": "registered"}} if ip else {}
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


def _tcp_listener() -> tuple[socket.socket, int]:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]

    def serve():
        for _ in range(8):
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                break

    threading.Thread(target=serve, daemon=True).start()
    return srv, port


def test_probe_cycle_marks_reachable_and_unreachable(client, db):
    srv, port = _tcp_listener()
    try:
        db.add(_mk("ast-alive", ip="127.0.0.1", port=port))
        db.add(_mk("ast-dead", ip="127.0.0.1", port=1))  # 端口 1 无监听 → 立即拒绝
        db.add(_mk("ast-noip"))
        db.commit()

        stats = run_probe_cycle(db)
        assert stats["total"] >= 3  # 库里还有 seed 演示资产
        assert stats["reachable"] >= 1

        alive = db.get(Asset, "ast-alive")
        assert alive.reachable is True
        assert alive.last_seen_at is not None
        assert alive.unreachable_reason == ""

        dead_row = db.get(Asset, "ast-dead")
        assert dead_row.reachable is False
        assert dead_row.last_seen_at is None
        assert "拒绝连接" in dead_row.unreachable_reason or "超时" in dead_row.unreachable_reason

        noip = db.get(Asset, "ast-noip")
        assert noip.reachable is False
        assert "未探测" in noip.unreachable_reason
    finally:
        srv.close()


def test_probe_via_api_and_list_fields(client, db):
    srv, port = _tcp_listener()
    try:
        db.add(_mk("ast-api-01", ip="127.0.0.1", port=port))
        db.commit()

        r = client.post("/api/v1/assets/probe")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1 and "reachable" in body

        row = client.get("/api/v1/assets", params={"keyword": "ast-api-01"}).json()["items"][0]
        assert row["reachable"] is True
        assert row["last_seen_at"]
        assert row["last_check_at"]
        # 详情接口也带探活字段
        detail = client.get("/api/v1/assets/ast-api-01").json()
        assert detail["last_seen_at"]
    finally:
        srv.close()


def test_probe_ip_fallback_from_hostname_or_id(db):
    """手工导入的资产没有 extra.provision.ip 时，应能从 hostname / 资产ID 提取 IP。"""
    from app.services.probe import _target_ip

    a1 = _mk("ast-host-ip")
    a1.hostname = "devops-mother-124.221.251.186"
    assert _target_ip(a1) == "124.221.251.186"

    a2 = _mk("node-10.0.0.12")
    assert _target_ip(a2) == "10.0.0.12"

    a3 = _mk("ast-noip")
    assert _target_ip(a3) == ""


def test_probe_ip_fallback_reaches_listener(client, db):
    """回退提取的 IP 应真实参与探测（母机式手工登记资产 → 可达）。"""
    srv, port = _tcp_listener()
    try:
        row = _mk("devops-mother-127.0.0.1")  # hostname 带 IP，extra 无 provision
        row.extra = {"probe": {"port": port}}  # 手工登记资产可用 extra.probe.port 指定探测端口
        db.add(row)
        db.commit()
        stats = run_probe_cycle(db)
        assert stats["reachable"] >= 1
        assert db.get(Asset, "devops-mother-127.0.0.1").reachable is True
    finally:
        srv.close()


def test_asset_metrics_mock_series(client, db):
    db.add(_mk("ast-any"))
    db.commit()
    r = client.get("/api/v1/assets/ast-any/metrics", params={"minutes": 60})
    assert r.status_code == 200
    body = r.json()
    assert body["real"] is False  # 测试环境为 mock 集成模式
    assert body["note"]
    for key in ("cpu", "mem", "disk", "load"):
        series = body["series"][key]
        assert len(series) >= 10
        assert series[-1]["v"] == body["latest"][key]


def test_asset_inspect_requires_ip(client, db):
    db.add(_mk("ast-no-ip"))
    db.commit()
    r = client.post(
        "/api/v1/assets/ast-no-ip/inspect",
        json={"username": "root", "password": "x", "port": 22},
    )
    assert r.status_code == 400
    assert "IP" in r.json()["detail"]


def test_asset_inspect_auth_failure(client, db):
    # 127.0.0.1 无 SSH → 连接失败（502 而非 5xx 崩溃）
    db.add(_mk("ast-ssh-fail", ip="127.0.0.1"))
    db.commit()
    r = client.post(
        "/api/v1/assets/ast-ssh-fail/inspect",
        json={"username": "root", "password": "x", "port": 1},
    )
    assert r.status_code in (401, 502)


# ---------------------------------------------------------------------------
# 服务器详情（sysinfo 全景采集）
# ---------------------------------------------------------------------------

_SYSINFO_RAW = """
@@BASIC@@
web-app-01
5.15.0-91-generic
x86_64
Ubuntu 22.04.3 LTS
2026-09-23 10:00:00 +0800
Asia/Shanghai
2026-09-01 08:30:12
@@VIRT@@
kvm
0
CBS-VM
Tencent Cloud
@@CPU@@
Architecture:            x86_64
CPU(s):                  8
Model name:              Intel(R) Xeon(R) Platinum 8255C CPU @ 2.50GHz
Socket(s):               1
Core(s) per socket:      4
Thread(s) per core:      2
CPU MHz:                 2500.000
CPU max MHz:             3400.0000
L3 cache:                16.5MB
NUMA node(s):            1
model name	: Intel(R) Xeon(R) Platinum 8255C CPU @ 2.50GHz
8
@@MEMINFO@@
MemTotal:        8000000 kB
MemFree:         2000000 kB
MemAvailable:    4000000 kB
Buffers:          300000 kB
Cached:          1500000 kB
Active:          3000000 kB
Inactive:        1000000 kB
Slab:             200000 kB
Mem:   7812 3900 1953 80 1179 3912
Swap:  1953    98 1855
@@DISKS@@
Filesystem      Size  Used Avail Use% Mounted on
/dev/vda1        40G   12G   26G  32% /
tmpfs           3.9G     0  3.9G   0% /dev/shm
@@INODES@@
Filesystem     Inodes IUsed IFree IUse% Mounted on
/dev/vda1      2560000 81000 2479000    4% /
tmpfs           999218     1  999217    1% /dev/shm
@@BLOCKS@@
NAME="vda" SIZE="40G" TYPE="disk" FSTYPE="" MOUNTPOINT="" MODEL=""
NAME="vda1" SIZE="40G" TYPE="part" FSTYPE="ext4" MOUNTPOINT="/" MODEL=""
@@IFACES@@
lo        UNKNOWN        127.0.0.1/8 ::1/128
eth0      UP             172.16.0.4/20 fe80::5400:2ff:fe9a:ba1/64
@@ROUTE@@
default via 172.16.0.1 dev eth0
172.16.0.0/20 dev eth0 proto kernel scope link src 172.16.0.4
@@TCP@@
Total: 30
TCP:   25 (estab 5, closed 0, orphaned 0, timewait 2)
UDP:   3
@@LISTEN@@
tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=800,fd=3))
tcp LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=900,fd=6))
@@PROCS@@
152
3
1
@@USERS@@
root pts/0 2026-09-23 09:00 (1.2.3.4)
@@AGENT@@
/usr/sbin/zabbix_agentd -c /etc/zabbix/zabbix_agentd.conf
zabbix_agentd (daemon) (Zabbix) 6.0.7
@@END@@
"""


class _FakeStream2:
    def read(self) -> bytes:
        return _SYSINFO_RAW.encode()


class _FakeSysSSH:
    def set_missing_host_key_policy(self, policy) -> None:  # noqa: ARG002
        return None

    def connect(self, *args, **kwargs) -> None:  # noqa: ARG002
        return None

    def exec_command(self, cmd: str, timeout: int | None = None):  # noqa: ARG002
        return None, _FakeStream2(), _FakeStream2()

    def close(self) -> None:
        return None


def test_collect_sysinfo_parses_all_sections(monkeypatch):
    from app.services import inspector

    monkeypatch.setattr(inspector.paramiko, "SSHClient", _FakeSysSSH)
    data = inspector.collect_sysinfo("10.0.0.5", 22, "root", "")

    # 基础信息
    assert data["basic"]["hostname"] == "web-app-01"
    assert data["basic"]["kernel"] == "5.15.0-91-generic"
    assert data["basic"]["arch"] == "x86_64"
    assert data["basic"]["os"] == "Ubuntu 22.04.3 LTS"
    assert data["basic"]["timezone"] == "Asia/Shanghai"
    assert data["basic"]["boot_at"] == "2026-09-01 08:30:12"
    # 虚拟化
    assert data["virt"]["label"] == "KVM 虚拟机"
    assert data["virt"]["product"] == "CBS-VM"
    # CPU：lscpu 主键 + 物理核=插槽×核
    assert "Xeon" in data["cpu"]["model"]
    assert data["cpu"]["cores_logical"] == "8"
    assert data["cpu"]["cores_physical"] == 4
    assert data["cpu"]["hyperthreading"] is True
    # 内存：free -m + /proc/meminfo 细分
    assert data["memory"]["mem"]["total_mb"] == 7812
    assert data["memory"]["mem"]["used_mb"] == 3900
    assert data["memory"]["swap"]["used_mb"] == 98
    assert data["memory"]["detail_kb"]["MemAvailable"] == 4000000
    # 磁盘：df -hP + df -iP 合并
    disks = {d["mount"]: d for d in data["disks"]}
    assert disks["/"]["pct"] == 32.0
    assert disks["/"]["inode_pct"] == 4.0
    assert disks["/dev/shm"]["size"] == "3.9G"
    # 块设备
    assert data["blocks"][0]["name"] == "vda"
    assert data["blocks"][1]["fstype"] == "ext4"
    # 网络
    ifaces = {i["iface"]: i for i in data["network"]["interfaces"]}
    assert ifaces["eth0"]["state"] == "UP"
    assert "172.16.0.4/20" in ifaces["eth0"]["addresses"]
    assert data["network"]["gateway"] == "172.16.0.1"
    assert len(data["network"]["routes"]) == 2
    assert data["network"]["tcp"]["estab"] == 5
    assert data["network"]["tcp"]["timewait"] == 2
    listen = {(l["proto"], l["local"]) for l in data["network"]["listening"]}
    assert ("tcp", "0.0.0.0:22") in listen
    procs = {p["process"] for p in data["network"]["listening"]}
    assert any(p.startswith("sshd") for p in procs)
    # 进程 / 会话 / Agent
    assert data["processes"] == {"total": 152, "running": 3, "zombie": 1}
    assert data["users"][0].startswith("root")
    assert data["zabbix_agent"]["running"] is True
    assert "6.0.7" in data["zabbix_agent"]["version"]


def test_asset_sysinfo_requires_ip(client, db):
    db.add(_mk("ast-sys-noip"))
    db.commit()
    r = client.post("/api/v1/assets/ast-sys-noip/sysinfo", json={})
    assert r.status_code == 400
    assert "IP" in r.json()["detail"]


def test_asset_sysinfo_connect_failure(client, db):
    db.add(_mk("ast-sys-fail", ip="127.0.0.1"))
    db.commit()
    r = client.post("/api/v1/assets/ast-sys-fail/sysinfo", json={"port": 1, "password": "x"})
    assert r.status_code in (502, 503)
