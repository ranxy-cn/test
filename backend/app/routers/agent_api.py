"""自研子机 Agent 接入体系（替代 Zabbix agent 链路）。

- POST /api/v1/agent/report        子机 agent 数据上报（token 认证，支持断网补发批量）
- GET  /api/v1/agent/config        子机 agent 拉取自身采集/推送配置
- POST /api/v1/assets/{id}/agent/token    管理端：生成/重置令牌
- GET  /api/v1/assets/{id}/agent/status   管理端：在线状态 + 最新指标
- PUT  /api/v1/assets/{id}/agent/config   管理端：更新采集/推送配置（资产级覆盖）
- DEL  /api/v1/assets/{id}/agent/config   管理端：清除覆盖，回到全局默认
- GET/PUT /api/v1/agent-config/defaults   管理端：全局默认采集/推送配置
- POST /api/v1/assets/{id}/agent/deploy   管理端：经 SSH 向子机下发部署（py/go）

认证：请求头 X-Agent-Token 与资产 extra["agent_token"] 匹配。
数据隔离：上报样本落 system_metric_samples（asset_id 维度），
母机本机采样 asset_id 为 NULL，互不混淆。
"""
from __future__ import annotations

import secrets
import threading
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AppSetting, Asset, SystemMetricSample, utcnow
from app.routers.deps import CurrentUser, require_perm
from app.services.audit import add_audit
from app.services.alert_policy import effective_policy
from sqlalchemy import select

router = APIRouter()

# 最新一帧内存缓存：{asset_id: {...}}，realtime 端点直读，避免高频查库
LATEST: dict[str, dict] = {}
_LATEST_LOCK = threading.Lock()

# 服务器详情全景缓存：{asset_id: {...}}，由 agent 低频上报 sysinfo 原始文本解析而来
SYSINFO: dict[str, dict] = {}
_SYSINFO_LOCK = threading.Lock()

DEFAULT_AGENT_CONFIG = {
    "report_interval": 5,   # 上报间隔（秒）
    "collect_interval": 5,  # 采集间隔（秒）
    "collect_items": ["cpu", "mem", "disk", "load", "net", "procs"],
    "buffer_max": 600,      # 断网本地缓存上限（条）
    "config_refresh": 30,   # 配置拉取间隔（秒）：策略/配置变更最迟约 30 秒同步到子机
    "offline_after": 30,    # 超过该秒数无上报视为离线
}

# 系统级全局默认配置的 kv 键（app_settings 表）；无记录时用代码内置默认
GLOBAL_CFG_KEY = "agent_config_default"

# 数值配置项（秒/条），管理端写入时收敛到 [1, 86400]
_INT_KEYS = ("report_interval", "collect_interval", "buffer_max", "config_refresh", "offline_after")
# 采集项白名单：基础 5 项 + 进程归因 + v1.1 扩展项（由告警策略开关驱动是否采集）
_COLLECT_ITEMS = (
    "cpu", "mem", "disk", "load", "net", "procs",
    "swap", "inode", "disk_io", "tcp", "oom", "process", "port", "net_probe", "bandwidth", "metrics",
)

# 帧内 ext 扩展字段白名单（进 SystemMetricSample.ext，供告警引擎判定）
_EXT_NUM_KEYS = (
    "swap", "inode", "await_ms", "tcp_tw", "tcp_total", "tcp_conn_pct",
    "loss_pct", "latency_ms", "bw_rx_pct", "bw_tx_pct", "oom_events",
    "load5", "load15", "ncpu", "mem_total_mb", "mem_used_mb",
)
_EXT_LIST_KEYS = ("procs_missing", "ports_down", "oom_detail")
# JSON 列表（dict 元素）：进程归因 [{pid, comm, cpu, mem}]
_EXT_JSON_KEYS = ("procs",)


def _clean_procs(v) -> list:
    """收敛进程归因列表：[{pid,ppid,comm,cpu,mem,user,args,etime}]，最多 12 条。"""
    out: list = []
    for p in (v or [])[:24]:
        if not isinstance(p, dict):
            continue
        try:
            out.append({
                "pid": int(p.get("pid") or 0),
                "ppid": int(p.get("ppid") or 0),
                "comm": str(p.get("comm") or "")[:64],
                "cpu": round(float(p.get("cpu") or 0), 2),
                "mem": round(float(p.get("mem") or 0), 2),
                "user": str(p.get("user") or "")[:32],
                "args": str(p.get("args") or "")[:256],
                "etime": str(p.get("etime") or "")[:32],
            })
        except (TypeError, ValueError):
            continue
    return out


def _ext_of(sample: dict) -> dict | None:
    """从上报帧提取扩展指标（白名单收敛，防注入任意数据）。"""
    ext: dict = {}
    for k in _EXT_NUM_KEYS:
        v = sample.get(k)
        if isinstance(v, (int, float)):
            ext[k] = round(float(v), 3)
    for k in _EXT_LIST_KEYS:
        v = sample.get(k)
        if isinstance(v, list):
            ext[k] = [str(x)[:128] for x in v[:32]]
    for k in _EXT_JSON_KEYS:
        v = sample.get(k)
        if isinstance(v, list):
            cleaned = _clean_procs(v)
            if cleaned:
                ext[k] = cleaned
    m = sample.get("metrics")
    if isinstance(m, dict):
        clean: dict = {}
        for name, val in list(m.items())[:128]:
            if isinstance(val, (int, float)):
                clean[str(name)[:128]] = round(float(val), 4)
        if clean:
            ext["metrics"] = clean
    return ext or None


def _global_cfg(db: Session | None) -> dict:
    if db is None:
        return {}
    row = db.get(AppSetting, GLOBAL_CFG_KEY)
    return dict(row.value or {}) if row else {}


def agent_cfg_of(asset: Asset, db: Session | None = None) -> dict:
    """生效配置 = 代码内置默认 ← 全局默认（管理员可调） ← 资产级覆盖。"""
    cfg = dict(DEFAULT_AGENT_CONFIG)
    cfg.update(_global_cfg(db))
    cfg.update((asset.extra or {}).get("agent_config") or {})
    return cfg


def _sanitize_cfg(patch: dict) -> dict:
    """管理端配置项收敛：数值 clamp 到 [1, 86400]，采集项白名单过滤（空则全集）。"""
    out: dict = {}
    for k in DEFAULT_AGENT_CONFIG:
        if k not in patch:
            continue
        v = patch[k]
        if k in _INT_KEYS:
            try:
                v = max(1, min(int(v), 86400))
            except (TypeError, ValueError):
                continue
        elif k == "collect_items":
            v = sorted({x for x in (v or []) if x in _COLLECT_ITEMS}, key=_COLLECT_ITEMS.index) or list(DEFAULT_AGENT_CONFIG["collect_items"])
        out[k] = v
    return out


def _get_asset_by_token(db: Session, asset_id: str, token: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if not asset or not (asset.extra or {}).get("agent_token"):
        raise HTTPException(401, "未知资产或未启用 agent")
    if not secrets.compare_digest(str(asset.extra["agent_token"]), token or ""):
        raise HTTPException(401, "令牌无效")
    return asset


class ReportIn(BaseModel):
    """单帧或多帧（断网补发）上报。sysinfo 为服务器详情原始文本（低频附带）。"""

    asset_id: str
    agent_version: str = ""
    samples: list[dict] = Field(default_factory=list)
    sysinfo: str = ""


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
            ext=_ext_of(s),
            source="agent",
        )
        rows.append(row)
        with _LATEST_LOCK:
            LATEST[asset.id] = {**s, "ts": ts or now.timestamp()}
    if body.sysinfo:
        # 低频附带的服务器详情原始文本：解析为全景 JSON 缓存（替代 SSH 上机采集）
        try:
            from app.services.inspector import parse_sysinfo_raw

            last_ip = (body.samples[-1] or {}).get("ip", "") if body.samples else ""
            info = parse_sysinfo_raw(body.sysinfo, ip=last_ip, username="agent")
            with _SYSINFO_LOCK:
                SYSINFO[asset.id] = info
        except Exception:  # noqa: BLE001 解析失败不影响采样落库
            pass
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
    """子机拉取采集/推送配置 + 生效告警策略（配置变更无需重启 agent 即生效）。"""
    _get_asset_by_token(db, asset_id, x_agent_token)
    asset = db.get(Asset, asset_id)
    policy, _source = effective_policy(db, asset)
    return {"config": agent_cfg_of(asset, db), "alert_policy": policy}


def agent_status_of(asset: Asset, db: Session | None = None) -> dict:
    """计算 agent 在线状态（查询时判定，无需后台任务）。"""
    info = (asset.extra or {}).get("agent") or {}
    cfg = agent_cfg_of(asset, db)
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
    return agent_status_of(asset, db)


@router.put("/api/v1/assets/{asset_id}/agent/config")
def agent_update_config(
    asset_id: str,
    body: dict,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """更新单台子机的采集/推送配置（资产级覆盖全局默认）。"""
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    patch = _sanitize_cfg(body)
    merged = agent_cfg_of(asset, db)
    merged.update(patch)
    asset.extra = {**(asset.extra or {}), "agent_config": merged}
    db.commit()
    add_audit(
        db,
        ticket_id=None,
        event_type="agent_config_update",
        actor=current.username,
        result={"asset_id": asset_id, "config": merged},
    )
    db.commit()
    return {"asset_id": asset_id, "config": merged}


@router.delete("/api/v1/assets/{asset_id}/agent/config")
def agent_reset_config(
    asset_id: str,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """清除子机级覆盖，回到全局默认配置（agent 下轮 config_refresh 自动生效）。"""
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    extra = dict(asset.extra or {})
    removed = extra.pop("agent_config", None)
    if removed is not None:
        asset.extra = extra
        db.commit()
        add_audit(
            db,
            ticket_id=None,
            event_type="agent_config_reset",
            actor=current.username,
            result={"asset_id": asset_id, "removed": removed},
        )
        db.commit()
    return {"asset_id": asset_id, "config": agent_cfg_of(asset, db)}


@router.get("/api/v1/agent-config/defaults", dependencies=[Depends(require_perm("assets:read"))])
def agent_config_defaults(db: Session = Depends(get_db)):
    """全局默认采集/推送配置 + 各子机覆盖清单（配置管理界面数据源）。"""
    overrides = [
        {"asset_id": a.id, "hostname": a.hostname, "config": (a.extra or {}).get("agent_config") or {}}
        for a in db.scalars(select(Asset)).all()
        if (a.extra or {}).get("agent_config")
    ]
    return {
        "config": {**DEFAULT_AGENT_CONFIG, **_global_cfg(db)},
        "built_in": dict(DEFAULT_AGENT_CONFIG),
        "overrides": overrides,
    }


@router.put("/api/v1/agent-config/defaults")
def agent_update_config_defaults(
    body: dict,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """保存全局默认配置（对未单独覆盖的子机生效，agent 定期拉取自动应用）。"""
    sanitized = _sanitize_cfg(body)
    row = db.get(AppSetting, GLOBAL_CFG_KEY)
    if row is None:
        row = AppSetting(key=GLOBAL_CFG_KEY, value={})
        db.add(row)
    row.value = sanitized
    db.commit()
    add_audit(
        db,
        ticket_id=None,
        event_type="agent_config_defaults_update",
        actor=current.username,
        result={"config": sanitized},
    )
    db.commit()
    return {"config": {**DEFAULT_AGENT_CONFIG, **sanitized}}


class AgentDeployIn(BaseModel):
    lang: str = "py"  # py | go
    ssh_user: str = "root"
    ssh_password: str = ""
    server_url: str = ""  # agent 回连地址，缺省 http://<母机IP>:8000


@router.post("/api/v1/assets/{asset_id}/agent/deploy", dependencies=[Depends(require_perm("assets:write"))])
def agent_deploy(asset_id: str, body: AgentDeployIn, request: Request, db: Session = Depends(get_db)):
    """经 SSH 向子机下发 agent（后台执行，进度写 extra["agent_deploy"]）。

    server_url 缺省时回退 平台公网地址 > 请求根地址（与新增母机自动纳管一致），
    避免把空地址写进 agent config 导致「已启动但永不上报」。
    """
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    if body.lang not in ("py", "go"):
        raise HTTPException(422, "lang 仅支持 py / go")
    ip = ((asset.extra or {}).get("provision") or {}).get("ip") or ""
    if not ip:
        raise HTTPException(409, "资产缺少 SSH 目标 IP，请先完成纳管")

    server_url = (
        body.server_url
        or (get_settings().platform_public_url or "").strip()
        or str(request.base_url).rstrip("/")
    )

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
                server_url=server_url,
                cfg=agent_cfg_of(db2.get(Asset, asset_id), db2),
            )
        except Exception as exc:  # noqa: BLE001 进度落库
            asset2 = db2.get(Asset, asset_id)
            if asset2:
                prov = (asset2.extra or {}).get("provision") or {}
                asset2.extra = {
                    **(asset2.extra or {}),
                    "provision": {**prov, "status": "failed", "error": str(exc)[:500]},
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


def sysinfo_of(asset_id: str) -> dict | None:
    """读取 agent 低频上报的服务器详情全景缓存（替代 SSH 上机采集）。"""
    with _SYSINFO_LOCK:
        info = SYSINFO.get(asset_id)
    return dict(info) if info else None


def inspect_snapshot_of(asset_id: str) -> dict | None:
    """从 agent 最新帧组装巡检快照（替代 SSH inspect_host）。无数据返回 None。"""
    with _LATEST_LOCK:
        latest = dict(LATEST.get(asset_id) or {})
    if not latest:
        return None
    procs = _clean_procs(latest.get("procs") or [])
    top_cpu = sorted([p for p in procs if p], key=lambda p: p["cpu"], reverse=True)[:15]
    top_mem = sorted([p for p in procs if p], key=lambda p: p["mem"], reverse=True)[:15]
    total_mb = latest.get("mem_total_mb")
    mem = {}
    if total_mb:
        mem = {
            "total_mb": int(total_mb),
            "used_mb": int(latest.get("mem_used_mb") or 0),
            "pct": latest.get("mem"),
        }
    return {
        "ip": latest.get("ip", ""),
        "load": {
            "load1": latest.get("load1"),
            "load5": latest.get("load5"),
            "load15": latest.get("load15"),
            "cores": latest.get("ncpu"),
        },
        "uptime_seconds": latest.get("uptime_seconds"),
        "mem": mem,
        "disk": {"pct": latest.get("disk")},
        "top_cpu": top_cpu,
        "top_mem": top_mem,
    }
