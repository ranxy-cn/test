"""资产连通性定时探测：对每台资产做 TCP 端口探活，回写最后连通时间与不通原因。

探测方式：TCP connect 目标 IP 的 SSH 端口（默认 22，可用 extra["probe"]["port"] 覆盖）。
不需要 SSH 凭据，容器内可直接执行；IP 取 extra["provision"]["ip"]，没有则跳过并标注原因。
"""
from __future__ import annotations

import re
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Asset, utcnow

PROBE_TIMEOUT = 3.0
PROBE_WORKERS = 16
REASON_NO_IP = "未探测：资产未登记 IP（自动纳管或导入时补充）"

_IP_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")


def _target_ip(asset: Asset) -> str:
    """依次从 extra.provision.ip / hostname / 资产ID 提取 IP（node-<IP> 规则）。"""
    prov = (asset.extra or {}).get("provision") or {}
    ip = str(prov.get("ip") or "").strip()
    if ip:
        return ip
    for text in (asset.hostname or "", asset.id or ""):
        m = _IP_RE.search(text or "")
        if m:
            return m.group(0)
    return ""


def _tcp_probe(ip: str, port: int, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """返回 (是否可达, 不通原因)。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True, ""
    except socket.timeout:
        return False, f"连接 {ip}:{port} 超时（{timeout:.0f}s），主机宕机或网络/防火墙不通"
    except ConnectionRefusedError:
        return False, f"{ip}:{port} 拒绝连接（SSH 服务未运行？）"
    except OSError as exc:
        return False, f"{ip}:{port} 连接失败：{exc}"


def probe_one(asset_id: str, ip: str, port: int = 22) -> dict:
    """纯探测（不做 DB 写入），供 celery task 与接口复用。"""
    if not ip:
        return {"asset_id": asset_id, "reachable": False, "reason": REASON_NO_IP, "checked_at": utcnow()}
    ok, reason = _tcp_probe(ip, port)
    return {
        "asset_id": asset_id,
        "reachable": ok,
        "reason": reason,
        "checked_at": utcnow(),
    }


def _asset_probe_target(asset: Asset) -> tuple[str, str, int]:
    extra = asset.extra or {}
    prov = extra.get("provision") or {}
    port = int(prov.get("port") or (extra.get("probe") or {}).get("port") or 22)
    return asset.id, _target_ip(asset), port


def run_probe_cycle(db: Session) -> dict:
    """对全部资产做一轮探测并回写状态。返回本轮统计。"""
    assets = db.query(Asset).all()
    targets = [_asset_probe_target(a) for a in assets]
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        results = list(
            pool.map(lambda t: probe_one(t[0], t[1], t[2]), targets)
        )
    by_id = {r["asset_id"]: r for r in results}
    reachable_n = 0
    for asset in assets:
        r = by_id.get(asset.id)
        if not r:
            continue
        asset.reachable = bool(r["reachable"])
        asset.last_check_at = r["checked_at"]
        if r["reachable"]:
            asset.last_seen_at = r["checked_at"]
            asset.unreachable_reason = ""
            reachable_n += 1
        else:
            asset.unreachable_reason = (r["reason"] or "")[:250]
    db.commit()
    return {
        "total": len(assets),
        "reachable": reachable_n,
        "unreachable": len(assets) - reachable_n,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
