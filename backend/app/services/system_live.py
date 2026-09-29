"""系统资源实时监控（类 macOS 活动监视器数据源）。

- 读取：直接解析 /proc（CPU /proc/stat、内存 /proc/meminfo、负载 /proc/loadavg、
  网速 /proc/net/dev、磁盘 statvfs），1 秒级开销可忽略，无需外部依赖。
- 宿主机视角：容器内 /proc 的 CPU/内存/负载本就是宿主机全局值；网络计数器是
  网络命名空间隔离的，生产把宿主机 /proc 只读挂载到 /host/proc 后读宿主机网口。
- 网速/CPU% 为相邻两次快照的差值速率；后台采样线程每 5 秒落库一行（应用启动后
  开始记录，不回填历史），保留 7 天。
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

from app.models import SystemMetricSample, utcnow

# 测试可覆盖；生产 api 容器把宿主机 /proc 挂载到 /host/proc
PROC_ROOT_OVERRIDE = os.environ.get("HOST_PROC", "")
_PREFERRED_PATHS = ("/host/proc", "/proc")

_LOCK = threading.Lock()
_prev: dict | None = None  # 上一次快照（计算 CPU% 与网速差值）

_SKIP_IFACES = ("lo", "docker", "veth", "br-", "virbr", "tun", "tap", "flannel", "cni", "cali")


def proc_root() -> str:
    """定位可用的 /proc 根：显式覆盖 > /host/proc > /proc；空串表示不支持。"""
    if PROC_ROOT_OVERRIDE:
        return PROC_ROOT_OVERRIDE if os.path.exists(os.path.join(PROC_ROOT_OVERRIDE, "stat")) else ""
    for p in _PREFERRED_PATHS:
        if os.path.exists(os.path.join(p, "stat")):
            return p
    return ""


def supported() -> bool:
    return bool(proc_root())


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _parse_cpu_jiffies(root: str) -> int | None:
    """/proc/stat 首行累计 jiffies（total 与 idle 分别返回）。"""
    for line in _read(os.path.join(root, "stat")).splitlines():
        if line.startswith("cpu "):
            cols = [int(x) for x in line.split()[1:]]
            if len(cols) < 5:
                return None
            idle = cols[3] + (cols[4] if len(cols) > 4 else 0)  # idle + iowait
            return sum(cols), idle
    return None


def _parse_meminfo(root: str) -> dict:
    info = {}
    for line in _read(os.path.join(root, "meminfo")).splitlines():
        parts = line.replace(":", " ").split()
        if parts and parts[0] in ("MemTotal", "MemAvailable"):
            info[parts[0]] = int(parts[1])  # kB
    return info


def _parse_loadavg(root: str) -> tuple[float, float, float] | None:
    parts = _read(os.path.join(root, "loadavg")).split()
    if len(parts) < 3:
        return None
    return float(parts[0]), float(parts[1]), float(parts[2])


def _primary_iface(root: str) -> str:
    """主网卡：默认路由所在接口；否则取排除虚拟网卡后流量最大的一个。"""
    try:
        for line in _read(os.path.join(root, "route")).splitlines()[1:]:
            cols = line.split()
            if len(cols) >= 8 and cols[0] == "00000000":  # 默认路由
                return cols[7]
    except OSError:
        pass
    best, best_rx = "", -1
    for ifname, stat in _parse_net_dev(root).items():
        low = ifname.lower()
        if any(low.startswith(s) or low == s for s in _SKIP_IFACES):
            continue
        if stat["rx"] > best_rx:
            best, best_rx = ifname, stat["rx"]
    return best


def _parse_net_dev(root: str) -> dict[str, dict]:
    """{iface: {rx, tx}} 累计字节数。"""
    out: dict[str, dict] = {}
    for line in _read(os.path.join(root, "net", "dev")).splitlines()[2:]:
        if ":" not in line:
            continue
        ifname, rest = line.split(":", 1)
        cols = rest.split()
        if len(cols) < 9:
            continue
        out[ifname.strip()] = {"rx": int(cols[0]), "tx": int(cols[8])}
    return out


def _disk_usage() -> dict | None:
    """根分区使用率：优先宿主机挂载路径（容器内 bind mount 指向宿主机盘）。"""
    import shutil

    for path in ("/app/knowledge", "/host/root", "/"):
        try:
            u = shutil.disk_usage(path)
            total, free = u.total, u.free
            if total > 0:
                return {
                    "path": path,
                    "pct": round((total - free) / total * 100, 2),
                    "used_gb": round(u.used / 1024**3, 2),
                    "total_gb": round(total / 1024**3, 2),
                }
        except OSError:
            continue
    return None


def read_snapshot() -> dict:
    """读一帧系统指标。CPU% 与网速相对上一次快照求差值；首次调用速率为 None。"""
    global _prev
    root = proc_root()
    now = time.time()
    snap: dict = {
        "ts": int(now),
        "supported": bool(root),
        "cpu": None,
        "mem": None,
        "mem_used_mb": None,
        "mem_total_mb": None,
        "disk": None,
        "disk_path": None,
        "disk_used_gb": None,
        "disk_total_gb": None,
        "load1": None,
        "load5": None,
        "load15": None,
        "net_iface": None,
        "net_rx_bps": None,
        "net_tx_bps": None,
        "note": "" if root else "当前环境无 /proc（仅支持 Linux 宿主机），无法实时采集",
    }
    if not root:
        return snap

    with _LOCK:
        prev = _prev
        cpu_res = _parse_cpu_jiffies(root)
        mem = _parse_meminfo(root)
        load = _parse_loadavg(root)
        iface = _primary_iface(root)
        net = _parse_net_dev(root).get(iface, {})
        disk = _disk_usage()

        if cpu_res:
            total, idle = cpu_res
            if prev and prev.get("cpu_total"):
                d_total, d_idle = total - prev["cpu_total"], idle - prev["cpu_idle"]
                if d_total > 0:
                    snap["cpu"] = round(max(0.0, min(100.0, (1 - d_idle / d_total) * 100)), 2)
        if mem.get("MemTotal"):
            total_kb, avail_kb = mem["MemTotal"], mem.get("MemAvailable", 0)
            snap["mem_total_mb"] = round(total_kb / 1024)
            snap["mem_used_mb"] = round((total_kb - avail_kb) / 1024)
            snap["mem"] = round((total_kb - avail_kb) / total_kb * 100, 2)
        if load:
            snap["load1"], snap["load5"], snap["load15"] = load
        if disk:
            snap.update(disk_path=disk["path"], disk=disk["pct"], disk_used_gb=disk["used_gb"], disk_total_gb=disk["total_gb"])
        if iface and net:
            snap["net_iface"] = iface
            if prev and prev.get("net_iface") == iface:
                dt = now - prev["t"]
                if dt > 0:
                    snap["net_rx_bps"] = round(max(0, net["rx"] - prev["net_rx"]) / dt, 2)
                    snap["net_tx_bps"] = round(max(0, net["tx"] - prev["net_tx"]) / dt, 2)
        _prev = {
            "t": now,
            "cpu_total": cpu_res[0] if cpu_res else None,
            "cpu_idle": cpu_res[1] if cpu_res else None,
            "net_iface": iface,
            "net_rx": net.get("rx", 0),
            "net_tx": net.get("tx", 0),
        }
    return snap


# ===== 后台采样落库（应用启动后开始记录，不回填历史） =====

_SAMPLER_STOP = threading.Event()
_SAMPLER_THREAD: threading.Thread | None = None


def _sample_interval() -> float:
    try:
        from app.config import get_settings

        return max(1.0, float(get_settings().system_sample_seconds))
    except Exception:  # noqa: BLE001  配置不可用时退回默认
        return 5.0


def _retention_days() -> int:
    try:
        from app.config import get_settings

        return max(1, int(get_settings().system_sample_retention_days))
    except Exception:  # noqa: BLE001
        return 7


def persist_sample(db) -> SystemMetricSample | None:
    """采一帧写一行（real）。/proc 不可用时跳过，保证不写全空数据。"""
    s = read_snapshot()
    if not s["supported"]:
        return None
    row = SystemMetricSample(
        ts=datetime.fromtimestamp(s["ts"], tz=timezone.utc),
        cpu=s["cpu"],
        mem=s["mem"],
        disk=s["disk"],
        load1=s["load1"],
        net_rx_bps=s["net_rx_bps"],
        net_tx_bps=s["net_tx_bps"],
        source="real",
    )
    db.add(row)
    db.commit()
    return row


def cleanup_old(db, days: int | None = None) -> int:
    """删除超过保留期的采样行，返回删除条数。"""
    from sqlalchemy import delete

    keep_days = days or _retention_days()
    cutoff = utcnow() - timedelta(days=keep_days)
    res = db.execute(delete(SystemMetricSample).where(SystemMetricSample.ts < cutoff))
    db.commit()
    return res.rowcount or 0


def _sampler_loop() -> None:
    from app.database import SessionLocal

    last_cleanup = 0.0
    while not _SAMPLER_STOP.is_set():
        if _SAMPLER_STOP.wait(_sample_interval()):
            break
        db = SessionLocal()
        try:
            persist_sample(db)
            # 每小时清理一次过期数据
            if time.time() - last_cleanup > 3600:
                cleanup_old(db)
                last_cleanup = time.time()
        except Exception:  # noqa: BLE001  采样失败不中断循环
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        finally:
            db.close()


def start_sampler() -> bool:
    """启动后台采样线程（幂等）。返回是否实际启动。"""
    global _SAMPLER_THREAD
    if not supported():
        return False
    if _SAMPLER_THREAD and _SAMPLER_THREAD.is_alive():
        return False
    _SAMPLER_STOP.clear()
    _SAMPLER_THREAD = threading.Thread(target=_sampler_loop, name="system-metrics-sampler", daemon=True)
    _SAMPLER_THREAD.start()
    return True


def stop_sampler() -> None:
    _SAMPLER_STOP.set()
