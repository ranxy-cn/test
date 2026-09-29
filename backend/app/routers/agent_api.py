"""自研子机 Agent 接入体系（替代 Zabbix agent 链路）。

- POST /api/v1/agent/report        子机 agent 数据上报（token 认证，支持断网补发批量）
- GET  /api/v1/agent/config        子机 agent 拉取自身采集/推送配置
- POST /api/v1/assets/{id}/agent/token    管理端：生成/重置令牌
- GET  /api/v1/assets/{id}/agent/status   管理端：在线状态 + 最新指标
- PUT  /api/v1/assets/{id}/agent/config   管理端：更新采集/推送配置
- POST /api/v1/assets/{id}/agent/deploy   管理端：经 SSH 向子机下发部署（py/go）

认证：请求头 X-Agent-Token 与资产 extra["agent_token"] 匹配。
数据隔离：上报样本落 system_metric_samples（asset_id 维度），
母机本机采样 asset_id 为 NULL，互不混淆。
"""
from __future__ import annotations

import secrets
import threading
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Asset, SystemMetricSample, utcnow
from app.routers.deps import require_perm

router = APIRouter()

# 最新一帧内存缓存：{asset_id: {...}}，realtime 端点直读，避免高频查库
LATEST: dict[str, dict] = {}
_LATEST_LOCK = threading.Lock()

DEFAULT_AGENT_CONFIG = {
    "report_interval": 5,   # 上报间隔（秒）
    "collect_interval": 5,  # 采集间隔（秒）
    "collect_items": ["cpu", "mem", "disk", "load", "net"],
    "buffer_max": 600,      # 断网本地缓存上限（条）
    "config_refresh": 300,  # 配置拉取间隔（秒）
    "offline_after": 30,    # 超过该秒数无上报视为离线
}


def agent_cfg_of(asset: Asset) -> dict:
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg.update((asset.extra or {}).get("agent_config") or {})
    return cfg


def _get_asset_by_token(db: Session, asset_id: str, token: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if not asset or not (asset.extra or {}).get("agent_token"):
        raise HTTPException(401, "未知资产或未启用 agent")
    if not secrets.compare_digest(str(asset.extra["agent_token"]), token or ""):
        raise HTTPException(401, "令牌无效")
    return asset


class ReportIn(BaseModel):
    """单帧或多帧（断网补发）上报。"""

    asset_id: str
    agent_version: str = ""
    samples: list[dict] = Field(default_factory=list)


@router.post("/api/v1/agent/report")
def agent_report(
    body: ReportIn,
    db: Session = Depends(get_db),
    x_agent_token: str = Header(default=""),
):
    asset = _get_asset_by_token(db, body.asset_id, x_agent_token)
    now = utcnow()
    rows: list[SystemMetricSample] = []
    for s in body.samples[-200:]:  # 补发上限保护
        try:
            ts = float(s.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        row = SystemMetricSample(
            asset_id=asset.id,
            ts=now.fromtimestamp(ts, tz=now.tzinfo) if ts > 0 else now,
            cpu=_f(s.get("cpu")),
            mem=_f(s.get("mem")),
            disk=_f(s.get("disk")),
            load1=_f(s.get("load1")),
            net_rx_bps=_f(s.get("net_rx_bps")),
            net_tx_bps=_f(s.get("net_tx_bps")),
            source="agent",
        )
        rows.append(row)
        with _LATEST_LOCK:
            LATEST[asset.id] = {**s, "ts": ts or now.timestamp()}
    if rows:
        db.add_all(rows)
    asset.extra = {
        **(asset.extra or {}),
        "agent": {
            "last_seen": now.isoformat(),
            "last_ip": (body.samples[-1] or {}).get("ip", "") if body.samples else "",
            "version": body.agent_version,
        },
    }
    db.commit()
    return {"accepted": len(rows)}


def _f(v):
    try:
        return None if v is None else round(float(v), 3)
    except (TypeError, ValueError):
        return None


@router.get("/api/v1/agent/config")
def agent_config(
    asset_id: str = Query(...),
    db: Session = Depends(get_db),
    x_agent_token: str = Header(default=""),
):
    _get_asset_by_token(db, asset_id, x_agent_token)
    asset = db.get(Asset, asset_id)
    return {"config": agent_cfg_of(asset)}


def agent_status_of(asset: Asset) -> dict:
    """计算 agent 在线状态（查询时判定，无需后台任务）。"""
    info = (asset.extra or {}).get("agent") or {}
    cfg = agent_cfg_of(asset)
    last_seen = info.get("last_seen")
    online = False
    if last_seen:
        try:
            seen = utcnow().fromisoformat(last_seen)
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=utcnow().tzinfo)
            online = (utcnow() - seen) <= timedelta(seconds=cfg["offline_after"])
        except ValueError:
            pass
    latest = {}
    with _LATEST_LOCK:
        latest = dict(LATEST.get(asset.id) or {})
    return {
        "asset_id": asset.id,
        "enabled": bool((asset.extra or {}).get("agent_token")),
        "online": online,
        "last_seen": last_seen,
        "version": info.get("version", ""),
        "config": cfg,
        "latest": latest,
    }


@router.post("/api/v1/assets/{asset_id}/agent/token", dependencies=[Depends(require_perm("assets:write"))])
def agent_token(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    token = secrets.token_urlsafe(24)
    asset.extra = {**(asset.extra or {}), "agent_token": token}
    db.commit()
    return {"asset_id": asset_id, "token": token}


@router.get("/api/v1/assets/{asset_id}/agent/status", dependencies=[Depends(require_perm("assets:read"))])
def agent_status(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    return agent_status_of(asset)


@router.put("/api/v1/assets/{asset_id}/agent/config", dependencies=[Depends(require_perm("assets:write"))])
def agent_update_config(asset_id: str, body: dict, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    merged = agent_cfg_of(asset)
    for k in DEFAULT_AGENT_CONFIG:
        if k in body:
            v = body[k]
            if k in ("report_interval", "collect_interval", "buffer_max", "config_refresh", "offline_after"):
                v = max(1, min(int(v), 86400))
            elif k == "collect_items":
                v = [x for x in (v or []) if x in ("cpu", "mem", "disk", "load", "net")] or ["cpu", "mem", "disk", "load", "net"]
            merged[k] = v
    asset.extra = {**(asset.extra or {}), "agent_config": merged}
    db.commit()
    return {"asset_id": asset_id, "config": merged}


class AgentDeployIn(BaseModel):
    lang: str = "py"  # py | go
    ssh_user: str = "root"
    ssh_password: str = ""
    server_url: str = ""  # agent 回连地址，缺省 http://<母机IP>:8000


@router.post("/api/v1/assets/{asset_id}/agent/deploy", dependencies=[Depends(require_perm("assets:write"))])
def agent_deploy(asset_id: str, body: AgentDeployIn, db: Session = Depends(get_db)):
    """经 SSH 向子机下发 agent（后台执行，进度写 extra["agent_deploy"]）。"""
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    if body.lang not in ("py", "go"):
        raise HTTPException(422, "lang 仅支持 py / go")
    ip = ((asset.extra or {}).get("provision") or {}).get("ip") or ""
    if not ip:
        raise HTTPException(409, "资产缺少 SSH 目标 IP，请先完成纳管")

    token = (asset.extra or {}).get("agent_token") or secrets.token_urlsafe(24)
    asset.extra = {**(asset.extra or {}), "agent_token": token}
    asset.extra = {
        **asset.extra,
        "agent_deploy": {"state": "running", "lang": body.lang, "started": utcnow().isoformat(), "steps": []},
    }
    db.commit()

    def _work():
        from app.services import agent_deployer

        db2 = next(get_db())
        try:
            agent_deployer.deploy_to_host(
                db2,
                asset_id,
                ip=ip,
                lang=body.lang,
                ssh_user=body.ssh_user,
                ssh_password=body.ssh_password,
                token=token,
                server_url=body.server_url,
                cfg=agent_cfg_of(db2.get(Asset, asset_id)),
            )
        except Exception as exc:  # noqa: BLE001 进度落库
            asset2 = db2.get(Asset, asset_id)
            asset2.extra = {
                **(asset2.extra or {}),
                "agent_deploy": {**(asset2.extra or {}).get("agent_deploy", {}), "state": "failed", "error": str(exc)[:500]},
            }
            db2.commit()
        finally:
            db2.close()

    threading.Thread(target=_work, daemon=True).start()
    return {"asset_id": asset_id, "state": "running"}


@router.get("/api/v1/assets/{asset_id}/agent/deploy", dependencies=[Depends(require_perm("assets:read"))])
def agent_deploy_status(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    return {"asset_id": asset_id, **((asset.extra or {}).get("agent_deploy") or {"state": "idle"})}


def settings_snapshot() -> dict:
    s = get_settings()
    return {
        "system_sample_seconds": s.system_sample_seconds,
        "retention_days": s.system_sample_retention_days,
    }
