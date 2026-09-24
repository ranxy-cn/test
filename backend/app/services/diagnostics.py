"""异常时刻进程快照服务。

告警产生（webhook 收到异常，或详情页手动点「重新采集」）→ 后台线程 SSH 到目标机
→ 单次连接执行聚合命令，采集当时的进程全景：
CPU / 内存 TOP 进程（PID、父进程、用户、占比、运行时长、完整命令行）、
进程总数 / 运行 / 僵尸、D 状态（IO 等待）进程、系统负载与运行时长、
内存与 Swap、磁盘、监听端口、登录会话
→ 结构化存入 anomaly_events.diagnostics，前端详情抽屉展示。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import paramiko

from app.config import get_settings
from app.database import SessionLocal
from app.models import AnomalyEvent, Asset
from app.services.inspector import (
    INSPECT_KEY_PATH,
    _load_private_key,
    _parse_df,
    _parse_free_m,
    _parse_listen,
    _split_sections,
)

# 单次 SSH 采集总超时（秒）
CMD_TIMEOUT = 20

# 快照命令：一条 sh -c 聚合采集，@@MARK@@ 切分各段输出，减少 SSH 往返
SNAPSHOT_CMD = r"""
echo @@LOAD@@; uptime 2>/dev/null;
echo @@NCPU@@; nproc 2>/dev/null;
echo @@MEM@@; free -m 2>/dev/null | sed -n '2,3p';
echo @@DISK@@; df -hP 2>/dev/null | grep -vE ' (tmpfs|devtmpfs|overlay|squashfs|udev)$' | head -n 8;
echo @@PROCS@@; ps -eo stat --no-headers 2>/dev/null | wc -l; ps -eo stat --no-headers 2>/dev/null | grep -c '^R'; ps -eo stat --no-headers 2>/dev/null | grep -c '^Z';
echo @@TOPCPU@@; ps -eo pid,ppid,user:16,pcpu,pmem,etime,comm,args --sort=-pcpu --no-headers 2>/dev/null | head -n 15;
echo @@TOPMEM@@; ps -eo pid,ppid,user:16,pcpu,pmem,etime,comm,args --sort=-pmem --no-headers 2>/dev/null | head -n 15;
echo @@DSTATE@@; ps -eo pid,ppid,user:16,stat,etime,comm,args 2>/dev/null | awk 'NR==1 || $4 ~ /D/' | head -n 10;
echo @@LISTEN@@; ss -tulnpH 2>/dev/null | head -n 25;
echo @@USERS@@; who 2>/dev/null;
echo @@END@@
""".replace("\n", " ")

_LOAD_RE = re.compile(r"load average:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)")


def _parse_top_ps(block: str) -> list[dict[str, Any]]:
    """ps -eo pid,ppid,user,pcpu,pmem,etime,comm,args → 进程结构化列表。"""
    rows: list[dict[str, Any]] = []
    for line in block.strip().splitlines():
        parts = line.split(None, 7)
        if len(parts) < 7:
            continue
        try:
            rows.append(
                {
                    "pid": int(parts[0]),
                    "ppid": int(parts[1]),
                    "user": parts[2],
                    "cpu": float(parts[3]),
                    "mem": float(parts[4]),
                    "etime": parts[5],
                    "comm": parts[6],
                    "args": parts[7] if len(parts) > 7 else parts[6],
                }
            )
        except ValueError:
            continue
    return rows


def _parse_dstate(block: str) -> list[dict[str, Any]]:
    """D 状态（不可中断 IO 等待）进程列表；首行为表头跳过。"""
    rows: list[dict[str, Any]] = []
    for line in block.strip().splitlines():
        parts = line.split(None, 6)
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        rows.append(
            {
                "pid": int(parts[0]),
                "ppid": int(parts[1]),
                "user": parts[2],
                "stat": parts[3],
                "etime": parts[4],
                "comm": parts[5],
                "args": parts[6] if len(parts) > 6 else parts[5],
            }
        )
    return rows


def _parse_uptime(block: str) -> dict[str, Any]:
    """uptime 输出 → 负载 / 运行时长 / 登录用户数。"""
    out: dict[str, Any] = {}
    m = _LOAD_RE.search(block)
    if m:
        out["load1"], out["load5"], out["load15"] = (float(x) for x in m.groups())
    m = re.search(r"\bup\s+(.+?),\s*\d+\s+users?", block)
    if m:
        out["uptime_text"] = m.group(1).strip()
    m = re.search(r"(\d+)\s+users?", block)
    if m:
        out["users_logged"] = int(m.group(1))
    return out


# ---------------------------------------------------------------------------
# SSH 目标与凭据
# ---------------------------------------------------------------------------

def _resolve_target(db, anomaly: AnomalyEvent) -> dict[str, Any] | None:
    """从资产 extra.provision 解析 SSH 目标；密码取 provision.password，无密码则用免密私钥。"""
    asset = db.get(Asset, anomaly.asset_id) if anomaly.asset_id else None
    if asset is None:
        return None
    provision = (asset.extra or {}).get("provision") or {}
    ip = provision.get("ip") or asset.extra.get("ssh_host") if asset.extra else None
    if not ip:
        return None
    password = provision.get("password") or get_settings().diag_ssh_password
    return {
        "ip": ip,
        "port": int(provision.get("port") or 22),
        "username": provision.get("username") or "root",
        "password": password,
        "asset": asset.hostname,
    }


def _ssh_snapshot(ip: str, port: int, username: str, password: str, key_path: str = "") -> dict[str, Any]:
    """SSH 单次连接采集异常时刻快照，返回结构化 dict。任何失败抛异常，由调用方置 failed。"""
    pkey = _load_private_key(key_path) if (key_path and not password) else None
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            ip,
            port=port,
            username=username,
            password=password or None,
            pkey=pkey,
            timeout=8.0,
            banner_timeout=8.0,
            auth_timeout=8.0,
            allow_agent=False,
            look_for_keys=False,
        )
        _, stdout, _ = client.exec_command(SNAPSHOT_CMD, timeout=CMD_TIMEOUT * 2)
        raw = stdout.read().decode("utf-8", errors="replace")
    finally:
        client.close()

    s = _split_sections(raw)

    def _int_at(section: str, idx: int) -> int:
        try:
            return int(s.get(section, "").splitlines()[idx].strip())
        except (IndexError, ValueError):
            return 0

    load = _parse_uptime(s.get("LOAD", ""))
    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "system": {"ncpu": _int_at("NCPU", 0), **load},
        "memory": _parse_free_m(s.get("MEM", "")),
        "disks": _parse_df(s.get("DISK", ""), human=True),
        "processes": {"total": _int_at("PROCS", 0), "running": _int_at("PROCS", 1), "zombie": _int_at("PROCS", 2)},
        "top_cpu": _parse_top_ps(s.get("TOPCPU", "")),
        "top_mem": _parse_top_ps(s.get("TOPMEM", "")),
        "d_state": _parse_dstate(s.get("DSTATE", "")),
        "listening": _parse_listen(s.get("LISTEN", "")),
        "users": [l for l in s.get("USERS", "").splitlines() if l.strip()],
    }


def collect_for_anomaly(anomaly_id: int) -> None:
    """后台采集入口（自建 Session，独立于请求事务）。幂等：running 时跳过。"""
    db = SessionLocal()
    try:
        anomaly = db.get(AnomalyEvent, anomaly_id)
        if anomaly is None or anomaly.diag_status == "running":
            return
        target = _resolve_target(db, anomaly)
        if target is None:
            anomaly.diag_status = "failed"
            anomaly.diag_error = "无法确定 SSH 目标：资产未录入 SSH 信息（extra.provision）"
            db.commit()
            return

        anomaly.diag_status = "running"
        anomaly.diag_error = ""
        db.commit()

        snapshot = _ssh_snapshot(
            target["ip"],
            target["port"],
            target["username"],
            target["password"],
            key_path=INSPECT_KEY_PATH,
        )

        anomaly = db.get(AnomalyEvent, anomaly_id)
        if anomaly is None:
            return
        anomaly.diagnostics = {
            "target": {"ip": target["ip"], "username": target["username"], "asset": target["asset"]},
            **snapshot,
        }
        anomaly.diag_status = "done"
        anomaly.diag_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
            anomaly = db.get(AnomalyEvent, anomaly_id)
            if anomaly is not None:
                anomaly.diag_status = "failed"
                anomaly.diag_error = str(exc)[:500]
                db.commit()
        except Exception:
            pass
    finally:
        db.close()
