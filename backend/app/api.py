from __future__ import annotations

import csv
import hmac
import io
import logging
import threading
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.domain.catalog import dump_catalog
from app.models import (
    AiAuditLog,
    AlertEvent,
    AnomalyEvent,
    AnomalyLog,
    Asset,
    AuditLog,
    BackupJob,
    BEIJING_TZ,
    DigitalEmployee,
    MaintenanceWindow,
    Notification,
    Ticket,
    TicketEvent,
    TicketStatus,
    utcnow,
)
from app.routers.deps import CurrentUser, require_perm
from app.schemas import AnomalyOut, ApprovalIn, TicketEventOut, TicketOut, ZabbixWebhookIn
from app.services import provision as provision_svc
from app.services import diagnostics as diagnostics_svc
from app.services.alert_policy import (
    ALERT_LEVELS,
    DEFAULT_POLICY,
    LEVEL_COLORS,
    LEVEL_LABELS,
    POLICY_TEMPLATES,
    RULE_CATALOG,
    effective_policy,
    normalize_policy,
)
from app.services.audit import add_audit, add_event
from app.services.notify import notify_ticket
from app.services.pipeline import approve_ticket, dispatch_investigation, in_maintenance, reject_ticket
from app.services.tickets import make_idempotency_key, next_ticket_number
from pydantic import BaseModel, Field

log = logging.getLogger("devops.api")

router = APIRouter()


@router.get("/health")
def health():
    from app.integrations import describe_integrations

    return {
        "ok": True,
        "service": "devops-agent",
        "employee": get_settings().employee_id,
        "integrations": describe_integrations(),
    }


@router.get("/api/v1/playbooks")
def playbooks():
    return {"items": dump_catalog()}


@router.get("/api/v1/employee/{employee_id}")
def employee_profile(employee_id: str, db: Session = Depends(get_db)):
    row = db.get(DigitalEmployee, employee_id)
    if row is None:
        raise HTTPException(404, "数字员工不存在")

    total = db.scalar(select(func.count()).select_from(Ticket).where(Ticket.employee_id == employee_id)) or 0
    recovered = db.scalar(
        select(func.count()).select_from(Ticket).where(
            Ticket.employee_id == employee_id, Ticket.status == TicketStatus.recovered.value
        )
    ) or 0
    return {
        "id": row.id,
        "name": row.name,
        "team": row.team,
        "systems": row.systems,
        "manager": row.manager,
        "oncall": row.oncall,
        "skill_version": row.skill_version,
        "auth_expires_at": row.auth_expires_at,
        "status": row.status,
        "duties": row.duties,
        "slogan": "LLM 只做分析与建议 · 策略引擎做决定 · 执行器做动作 · 证据链做证明",
        "stats": {"tickets": total, "recovered": recovered},
    }


def _asset_row(a: Asset) -> dict:
    prov = (a.extra or {}).get("provision") or {}
    return {
        "id": a.id,
        "hostname": a.hostname,
        "app": a.app,
        "role": a.role,
        "env": a.env,
        "owner": a.owner,
        "group": a.group,
        "kind": a.kind,
        "mother_id": a.mother_id,
        "db_mode": a.db_mode,
        "tenant_id": a.tenant_id,
        "reachable": a.reachable,
        "db_ok": a.db_ok,
        "external_id": a.external_id,
        "ip": prov.get("ip", ""),
        "provision_status": prov.get("status", ""),
        "provision_logs": "\n".join(
            (prov.get("logs") or ((a.extra or {}).get("agent_deploy") or {}).get("steps", []))[-8:]
        ),
        "install_info": prov.get("install_info") or None,
        "last_seen_at": a.last_seen_at,
        "last_check_at": a.last_check_at,
        "unreachable_reason": a.unreachable_reason,
    }


@router.get("/api/v1/assets", dependencies=[Depends(require_perm("assets:read"))])
def list_assets(
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    keyword: str = Query(default="", max_length=128),
    env: str = Query(default="", max_length=32),
    role: str = Query(default="", max_length=64),
):
    """资产台账：分页 + 关键字（id/主机名/应用/负责人）+ 环境/角色过滤。"""
    q = db.query(Asset)
    if keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.filter(
            or_(
                Asset.id.like(kw),
                Asset.hostname.like(kw),
                Asset.app.like(kw),
                Asset.owner.like(kw),
            )
        )
    if env.strip():
        q = q.filter(Asset.env == env.strip())
    if role.strip():
        q = q.filter(Asset.role == role.strip())
    total = q.count()
    rows = q.order_by(Asset.id).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [_asset_row(a) for a in rows], "total": total, "page": page, "page_size": page_size}


ASSET_CSV_COLUMNS = ["id", "hostname", "external_id", "app", "role", "env", "owner"]


@router.get("/api/v1/assets/export", dependencies=[Depends(require_perm("assets:read"))])
def export_assets(db: Session = Depends(get_db)):
    """导出全部资产为 CSV（UTF-8 带 BOM，Excel 直接打开不乱码）。"""
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.DictWriter(buf, fieldnames=ASSET_CSV_COLUMNS)
    writer.writeheader()
    for a in db.scalars(select(Asset).order_by(Asset.id)).all():
        writer.writerow({c: getattr(a, c) or "" for c in ASSET_CSV_COLUMNS})
    filename = f"assets-{utcnow().strftime('%Y%m%d')}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class AssetImportResult(BaseModel):
    created: int = 0
    updated: int = 0
    failed: int = 0
    errors: list[dict] = []


@router.post("/api/v1/assets/import", dependencies=[Depends(require_perm("assets:write"))])
def import_assets(file: UploadFile, db: Session = Depends(get_db)) -> AssetImportResult:
    """导入资产 CSV（列同导出；按 id 幂等 upsert，hostname 缺失的行报错跳过）。"""
    result = AssetImportResult()
    raw = file.file.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or "id" not in [c.strip() for c in reader.fieldnames]:
        raise HTTPException(400, f"CSV 首行必须包含列头（至少 id），当前为: {reader.fieldnames}")
    for idx, row in enumerate(reader, start=2):
        data = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
        aid = data.get("id", "")
        if not aid or len(aid) > 64:
            result.failed += 1
            result.errors.append({"line": idx, "error": f"id 缺失或超长: {aid[:64]!r}"})
            continue
        if not data.get("hostname"):
            result.failed += 1
            result.errors.append({"line": idx, "error": "hostname 缺失"})
            continue
        asset = db.get(Asset, aid)
        if asset is None:
            asset = Asset(id=aid, tenant_id=get_settings().tenant_id, reachable=False)
            result.created += 1
        else:
            result.updated += 1
        for col in ASSET_CSV_COLUMNS:
            if col in ("id",):
                continue
            if col == "external_id" and not data.get(col):
                continue
            setattr(asset, col, data.get(col, "") or getattr(asset, col, ""))
        if not asset.env:
            asset.env = "prod"
        if not asset.role:
            asset.role = "app"
        db.add(asset)
    db.commit()
    return result


# mock 演示序列实现在 app/services/metrics_store.py（api 与 celery 采集任务共用）
from app.services.metrics_store import (  # noqa: E402
    baseline_bands,
    build_baseline,
    detect_anomalies,
    forecast_series,
    mock_asset_metrics as _mock_asset_metrics,
    query_compare,
    query_series,
)


def _children_agent_summary(rows: list[dict]) -> dict[str, dict[str, float | None]]:
    """批量取子机指标最新值（来自子机 agent 上报的内存缓存，无外部 RPC）。

    返回 {asset_id: {cpu, mem, disk, load, net_rx_bps, net_tx_bps}}；
    无 agent 数据的子机为 None（前端显示 "-"）。
    """
    from app.routers.agent_api import LATEST, _LATEST_LOCK

    out: dict[str, dict[str, float | None]] = {}
    with _LATEST_LOCK:
        for r in rows:
            frame = LATEST.get(r["id"])
            if not frame:
                continue
            out[r["id"]] = {
                "cpu": frame.get("cpu"),
                "mem": frame.get("mem"),
                "disk": frame.get("disk"),
                "load": frame.get("load1", frame.get("load")),
                "net_rx_bps": frame.get("net_rx_bps"),
                "net_tx_bps": frame.get("net_tx_bps"),
            }
    return out


def _overview_payload(db: Session, mother: Asset | None) -> dict:
    """母机总览：母机本体 + 其子机按业务分组的统计。

    子机口径：mother_id 指向该母机的子机。归属规则：存量未指定母机的子机
    归默认母机，保证平滑升级。
    """
    default_mother_id = get_settings().mother_asset_id
    mother_id = mother.id if mother else default_mother_id
    children = db.scalars(select(Asset).order_by(Asset.id)).all()
    rows = []
    for a in children:
        if a.id == mother_id or a.kind == "mother":
            continue
        belongs = a.mother_id == mother_id or (not a.mother_id and mother_id == default_mother_id)
        if not belongs:
            continue
        row = _asset_row(a)
        row.setdefault("metrics", None)
        rows.append(row)
    # 子机实时指标摘要（CPU/内存/磁盘/负载最新值，来自子机 agent 上报缓存）；无数据为 None，前端显示 "-"
    if rows:
        summary = _children_agent_summary(rows)
        for r in rows:
            if r["id"] in summary:
                r["metrics"] = summary[r["id"]]
    groups: dict[str, dict[str, int]] = {}
    for row in rows:
        g = groups.setdefault(row["group"] or "", {"total": 0, "reachable": 0})
        g["total"] += 1
        if row["reachable"]:
            g["reachable"] += 1
    # 母机上登记的自定义空分组也保留展示（还没有子机的分组）
    if mother is not None:
        for gname in (mother.extra or {}).get("custom_groups") or []:
            if gname and gname not in groups:
                groups[gname] = {"total": 0, "reachable": 0}
    # 未恢复告警数（子机卡片红色徽标）
    real_ids = [r["id"] for r in rows]
    abnormal: dict[str, int] = {}
    if real_ids:
        qres = (
            db.query(AnomalyEvent.asset_id, func.count())
            .filter(AnomalyEvent.asset_id.in_(real_ids), AnomalyEvent.status == "abnormal")
            .group_by(AnomalyEvent.asset_id)
            .all()
        )
        abnormal = {aid: int(n) for aid, n in qres}
    for r in rows:
        r["abnormal_count"] = abnormal.get(r["id"], 0)
    return {
        "mother": _asset_row(mother) if mother else None,
        "children": rows,
        "groups": [{"name": k, **v} for k, v in sorted(groups.items(), key=lambda kv: (kv[0] == "", kv[0]))],
    }


@router.get("/api/v1/assets/mothers", dependencies=[Depends(require_perm("assets:read"))])
def list_mothers(db: Session = Depends(get_db)):
    """母机列表（kind=mother），附各母机子机数量。注意路由顺序：须在 /assets/{asset_id} 之前。"""
    from app.routers.agent_api import agent_status_of

    settings = get_settings()
    items = []
    for a in db.scalars(select(Asset).where(Asset.kind == "mother").order_by(Asset.id)).all():
        row = _asset_row(a)
        is_default = a.id == settings.mother_asset_id
        row["is_default"] = is_default
        # 未归属母机的存量子机只计入默认母机
        q = db.query(Asset).filter(Asset.id != a.id, Asset.kind != "mother")
        q = q.filter(or_(Asset.mother_id == a.id, Asset.mother_id == "")) if is_default else q.filter(Asset.mother_id == a.id)
        row["children_count"] = q.count()
        # 母机在线 = 本机子机 agent 在线（新增母机自动纳管产生）；无本机子机时保持台账值
        self_child_id = str((a.extra or {}).get("self_child_id") or "")
        row["self_child_id"] = self_child_id
        if self_child_id:
            child = db.get(Asset, self_child_id)
            if child is not None:
                row["reachable"] = agent_status_of(child, db)["online"]
                row["self_child_online"] = row["reachable"]
        # 本机子机 agent 部署中：在线状态尚未确立，前端显示「纳管中」而非「离线」
        row["provisioning"] = ((a.extra or {}).get("provision") or {}).get("status") == "running" and not self_child_id
        items.append(row)
    return {"items": items, "default_id": settings.mother_asset_id}


@router.get("/api/v1/assets/mother", dependencies=[Depends(require_perm("assets:read"))])
def mother_overview(db: Session = Depends(get_db)):
    """默认母机总览（兼容旧前端入口）。"""
    mother = db.get(Asset, get_settings().mother_asset_id)
    return _overview_payload(db, mother)


class MotherCreateIn(BaseModel):
    """新增母机：SSH 验证 + 自动纳管本机子机。

    提供密码时：先验证 SSH 连通性（失败拒绝创建），成功后自动在母机下创建
    并纳管一台「本机子机」（部署自研 agent 采集真实指标），母机的在线状态与
    监控数据均来自该子机。不提供密码则退回旧的纯业务登记（仅 API 兼容，
    前端一律强制填写密码）。
    """

    hostname: str = Field(min_length=1, max_length=128)
    ip: str = Field(min_length=3, max_length=64)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default="root", max_length=64)
    password: str = Field(default="", max_length=128)  # 仅本次验证/部署使用，不落库
    env: str = Field(default="prod", max_length=32)
    owner: str = Field(default="", max_length=64)
    alert_policy: dict | None = None  # 告警策略（阈值/窗口），缺省字段用平台默认值


class SshTestIn(BaseModel):
    """SSH 连通性测试：新增母机/子机前手动验证凭据。密码仅本次使用，不落库。"""

    ip: str = Field(min_length=3, max_length=64)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default="root", max_length=64)
    password: str = Field(min_length=1, max_length=128)


def _verify_ssh(ip: str, port: int, username: str, password: str) -> int:
    """SSH 连通性验证：连接并执行 echo 探针。成功返回耗时 ms，失败抛 ProvisionError。"""
    import time as _time

    started = _time.monotonic()
    ssh = provision_svc._connect_ssh(ip, port, username, password)
    try:
        code, out = provision_svc._run(ssh, "echo __ssh_ok__", timeout=15, logs=[], pty=False)
        if "__ssh_ok__" not in out:
            raise provision_svc.ProvisionError("SSH 已连接但命令执行异常，请检查目标机 shell 可用性")
    finally:
        ssh.close()
    return round((_time.monotonic() - started) * 1000)


@router.post("/api/v1/assets/ssh-test", dependencies=[Depends(require_perm("assets:write"))])
def ssh_test(body: SshTestIn):
    """测试目标机 SSH 能否连通并执行命令（供新增母机/子机表单的「测试连接」按钮）。"""
    try:
        ms = _verify_ssh(body.ip.strip(), body.port, body.username.strip(), body.password)
    except provision_svc.ProvisionError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:
        # paramiko 偶发异常（SSH 会话刚建立即断的 SSHException/EOFError 等）也转可读 400，
        # 避免落到 500 纯文本导致前端只能显示笼统兜底文案
        raise HTTPException(400, f"SSH 会话异常：{exc}。请确认目标机 SSH 服务正常后重试") from None
    return {"ok": True, "latency_ms": ms, "message": f"连接成功，命令执行正常（{ms}ms）"}


@router.get("/api/v1/assets/mothers/{mother_id}/overview", dependencies=[Depends(require_perm("assets:read"))])
def mother_overview_by_id(mother_id: str, db: Session = Depends(get_db)):
    """指定母机的总览（母机切换器数据源）。"""
    mother = db.get(Asset, mother_id)
    if mother is None or mother.kind != "mother":
        raise HTTPException(404, "母机不存在")
    return _overview_payload(db, mother)


@router.get("/api/v1/assets/mothers/{mother_id}/children/realtime", dependencies=[Depends(require_perm("assets:read"))])
def children_realtime(mother_id: str, db: Session = Depends(get_db)):
    """母机名下全部子机的实时指标批量端点（分组卡片 1 秒轮询专用）。

    数据源与监控详情实时面板同源：agent 上报内存缓存 LATEST；online 口径同列表页
    （last_seen 未超 offline_after）。轻量设计：仅返回指标与在线态，供高频轮询。
    """
    from app.routers.agent_api import agent_status_of

    mother = db.get(Asset, mother_id)
    if mother is None or mother.kind != "mother":
        raise HTTPException(404, "母机不存在")
    default_mother_id = get_settings().mother_asset_id
    rows = []
    for a in db.scalars(select(Asset).where(Asset.mother_id == mother_id, Asset.kind != "mother")).all():
        rows.append(a)
    # 存量未归属母机的子机归默认母机（口径与 _overview_payload 一致）
    if mother_id == default_mother_id:
        for a in db.scalars(select(Asset).where(Asset.mother_id == "", Asset.kind != "mother")).all():
            rows.append(a)
    items: dict[str, dict] = {}
    for a in rows:
        status = agent_status_of(a, db)
        latest = status["latest"]
        items[a.id] = {
            "online": bool(status["online"]),
            "last_seen": status["last_seen"],
            "ts": latest.get("ts"),
            "cpu": latest.get("cpu"),
            "mem": latest.get("mem"),
            "disk": latest.get("disk"),
            "load": latest.get("load1", latest.get("load")),
            "net_rx_bps": latest.get("net_rx_bps"),
            "net_tx_bps": latest.get("net_tx_bps"),
        }
    return {"items": items}


@router.post("/api/v1/assets/mothers", dependencies=[Depends(require_perm("assets:write"))])
def create_mother(
    body: MotherCreateIn,
    request: Request,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """新增母机：SSH 验证 + 自动纳管本机子机（密码留空则仅登记，API 兼容路径）。"""
    import secrets as _secrets

    from app.routers.agent_api import agent_cfg_of

    try:
        alert_policy = normalize_policy(body.alert_policy)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    aid = f"mother-{body.ip.replace('.', '-')}"
    exists = db.get(Asset, aid)
    if exists is not None:
        raise HTTPException(409, f"该 IP 已登记为母机 {aid}")
    if db.query(Asset).filter(Asset.hostname == body.hostname).first():
        raise HTTPException(409, f"主机名已被资产占用：{body.hostname}")

    # SSH 验证前置：失败直接拒绝创建，避免错误 IP 的死台账
    use_ssh = bool(body.password.strip())
    verify_ms = 0
    if use_ssh:
        try:
            verify_ms = _verify_ssh(body.ip.strip(), body.ssh_port, body.username.strip(), body.password)
        except provision_svc.ProvisionError as exc:
            raise HTTPException(400, f"SSH 验证失败：{exc}") from None

    asset = Asset(
        id=aid,
        hostname=body.hostname.strip(),
        app="",
        role="mother",
        env=body.env,
        owner=body.owner,
        kind="mother",
        tenant_id=get_settings().tenant_id,
        reachable=False,  # 在线状态由本机子机 agent 推导（见 list_mothers），台账初始不可达
        extra={
            "alert_policy": alert_policy,
            "provision": {
                "ip": body.ip,
                "port": body.ssh_port,
                **({"username": body.username.strip()} if use_ssh else {}),
                "status": "registered",
            },
        },
    )
    db.add(asset)

    child_id = ""
    if use_ssh:
        # 自动纳管本机子机：归属该母机，部署自研 agent 采集真实指标（幂等复用同 ip:port 子机）
        cid = provision_svc.asset_id_for_ip(body.ip, body.ssh_port)
        child = db.get(Asset, cid)
        if child is None:
            if db.query(Asset).filter(Asset.id.like("node-%")).count():
                for node in db.query(Asset).filter(Asset.id.like("node-%")).all():
                    p = (node.extra or {}).get("provision") or {}
                    if (
                        str(p.get("ip") or "").strip().lower() == body.ip.strip().lower()
                        and int(p.get("port") or 22) == int(body.ssh_port)
                    ):
                        child = node
                        break
        if child is None:
            child = Asset(
                id=cid,
                hostname=f"{body.ip.strip()}:{body.ssh_port}",
                app="",
                role="other",
                env=body.env,
                owner=body.owner,
                kind="child",
                mother_id=aid,
                tenant_id=get_settings().tenant_id,
                reachable=False,
                extra={"provision": {"status": "running", "ip": body.ip, "port": body.ssh_port, "username": body.username.strip()}},
            )
        else:
            child.mother_id = aid
            extra = dict(child.extra or {})
            extra["provision"] = {"status": "running", "ip": body.ip, "port": body.ssh_port, "username": body.username.strip()}
            child.extra = extra
        token = _secrets.token_urlsafe(24)
        child.extra = {**(child.extra or {}), "agent_token": token}
        child_id = cid
        asset.extra = {**asset.extra, "self_child_id": child_id}
        db.add(child)
    db.commit()

    if use_ssh and child_id:
        server_url = (get_settings().platform_public_url or str(request.base_url).rstrip("/")).strip()
        cfg = agent_cfg_of(db.get(Asset, child_id), db)
        _spawn_agent_deploy(
            child_id,
            ip=body.ip,
            port=body.ssh_port,
            username=body.username.strip(),
            password=body.password,
            lang="go",
            token=token,
            server_url=server_url,
            cfg=cfg,
            requested_by=current.username,
        )

    add_audit(
        db,
        ticket_id=None,
        event_type="mother_create",
        actor=current.username,
        result={
            "asset_id": aid,
            "ip": body.ip,
            "ssh_verified": use_ssh,
            "ssh_latency_ms": verify_ms,
            "self_child_id": child_id,
        },
    )
    db.commit()
    row = _asset_row(asset)
    row.update({"self_child_id": child_id, "ssh_verified": use_ssh, "provision_started": bool(child_id)})
    return row


def _policy_meta() -> dict:
    """策略元数据：级别定义（含颜色）、规则目录、可选模板（配置界面渲染数据源）。"""
    return {
        "levels": [
            {"value": lv, "label": LEVEL_LABELS[lv], "color": LEVEL_COLORS[lv]}
            for lv in ALERT_LEVELS
        ],
        "catalog": RULE_CATALOG,
        "templates": [
            {"id": tid, "label": tpl["label"], "policy": normalize_policy(tpl["policy"])}
            for tid, tpl in POLICY_TEMPLATES.items()
        ],
    }


@router.get("/api/v1/alert-policy/meta", dependencies=[Depends(require_perm("assets:read"))])
def alert_policy_meta():
    """告警策略元数据：P0-P3 级别（颜色）/ 规则目录 / 配置模板。"""
    return _policy_meta()


@router.get("/api/v1/assets/mothers/{mother_id}/alert-policy", dependencies=[Depends(require_perm("assets:read"))])
def get_alert_policy(mother_id: str, db: Session = Depends(get_db)):
    """母机告警策略：平台存储值 + 默认值（纯存储，供本地越限判定与展示）。"""
    mother = db.get(Asset, mother_id)
    if mother is None or mother.kind != "mother":
        raise HTTPException(404, "母机不存在")
    return {
        "policy": normalize_policy((mother.extra or {}).get("alert_policy")),
        "defaults": normalize_policy(None),
        **_policy_meta(),
    }


@router.put("/api/v1/assets/mothers/{mother_id}/alert-policy", dependencies=[Depends(require_perm("assets:write"))])
def update_alert_policy(
    mother_id: str,
    body: dict,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """保存母机告警策略（纯存储，v2 秒制 + 规则目录；写审计日志）。"""
    mother = db.get(Asset, mother_id)
    if mother is None or mother.kind != "mother":
        raise HTTPException(404, "母机不存在")
    try:
        policy = normalize_policy(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    mother.extra = {**(mother.extra or {}), "alert_policy": policy}
    db.commit()
    add_audit(
        db,
        ticket_id=None,
        event_type="alert_policy_update",
        actor=current.username,
        result={"mother_id": mother_id, "policy": policy},
    )
    db.commit()
    return {"policy": policy}


@router.get("/api/v1/assets/{asset_id}/alert-policy", dependencies=[Depends(require_perm("assets:read"))])
def get_asset_alert_policy(asset_id: str, db: Session = Depends(get_db)):
    """资产告警策略：母机=自身存储值；子机=自有值，未设置时继承母机（inherited=true）。"""
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "资产不存在")
    own = (asset.extra or {}).get("alert_policy")
    policy, source = effective_policy(db, asset)
    return {
        "policy": policy,
        "inherited": own is None,
        "source": source,
        "defaults": normalize_policy(None),
        **_policy_meta(),
    }


@router.put("/api/v1/assets/{asset_id}/alert-policy", dependencies=[Depends(require_perm("assets:write"))])
def update_asset_alert_policy(
    asset_id: str,
    body: dict,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """保存资产告警策略（v2 秒制 + 规则目录）：母机/子机均写自身 extra.alert_policy。

    子机自有策略只影响该子机；保存写审计日志；agent 下轮 config_refresh 拉取即生效。
    """
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "资产不存在")
    try:
        policy = normalize_policy(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    asset.extra = {**(asset.extra or {}), "alert_policy": policy}
    db.commit()
    add_audit(
        db,
        ticket_id=None,
        event_type="alert_policy_update",
        actor=current.username,
        result={"asset_id": asset_id, "kind": asset.kind, "policy": policy},
    )
    db.commit()
    return {"policy": policy, "inherited": False}


@router.delete("/api/v1/assets/{asset_id}/alert-policy", dependencies=[Depends(require_perm("assets:write"))])
def reset_asset_alert_policy(
    asset_id: str,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """清空资产自有策略：子机恢复继承母机，母机恢复平台默认。"""
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "资产不存在")
    extra = {k: v for k, v in (asset.extra or {}).items() if k != "alert_policy"}
    asset.extra = extra
    db.commit()
    add_audit(
        db,
        ticket_id=None,
        event_type="alert_policy_reset",
        actor=current.username,
        result={"asset_id": asset_id, "kind": asset.kind},
    )
    db.commit()
    policy, source = effective_policy(db, asset)
    return {"policy": policy, "inherited": asset.kind == "child", "source": source}


@router.post("/api/v1/assets/mothers/{mother_id}/uninstall", dependencies=[Depends(require_perm("assets:write"))])
def uninstall_mother(
    mother_id: str,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """删除母机：仅删除台账记录；名下仍有子机时拒绝（必须先删除全部子机）。

    被工单/维护窗口/备份任务引用时直接拒绝（提交时校验）。
    """
    a = db.get(Asset, mother_id)
    if a is None or a.kind != "mother":
        raise HTTPException(404, "母机不存在")
    children = _mother_children(db, mother_id)
    if children:
        raise HTTPException(
            400,
            f"该母机名下仍有 {len(children)} 台子机，请先删除全部子机后再删除母机",
        )
    _assert_refs_free(db, [a])
    db.delete(a)
    add_audit(
        db,
        ticket_id=None,
        event_type="mother_uninstall",
        actor=current.username,
        result={"asset_id": mother_id, "status": "deleted", "children_blocked": False},
    )
    db.commit()
    return {"deleted": mother_id, "cascade_children": []}


class GroupRenameIn(BaseModel):
    """分组重命名：该母机名下子机的 group 与母机上的自定义空分组一并更新。"""

    name: str = Field(min_length=1, max_length=64)
    new_name: str = Field(min_length=1, max_length=64)


class GroupDeleteIn(BaseModel):
    """删除空分组：分组下仍有子机时拒绝。"""

    name: str = Field(min_length=1, max_length=64)


def _scoped_group_query(db: Session, mother_id: str, group: str):
    """该母机名下（含未指定母机的存量子机）处于指定分组的子机查询。"""
    return (
        db.query(Asset)
        .filter(Asset.kind != "mother", Asset.group == group)
        .filter(or_(Asset.mother_id == mother_id, Asset.mother_id.is_(None)))
    )


@router.post("/api/v1/assets/mothers/{mother_id}/groups/rename", dependencies=[Depends(require_perm("assets:write"))])
def rename_group(mother_id: str, body: GroupRenameIn, db: Session = Depends(get_db)):
    mother = db.get(Asset, mother_id)
    if mother is None or mother.kind != "mother":
        raise HTTPException(404, "母机不存在")
    new_name = body.new_name.strip()[:64]
    if not new_name:
        raise HTTPException(422, "新分组名不能为空")
    if body.name == new_name:
        return {"ok": True, "moved": 0}
    customs = [g for g in ((mother.extra or {}).get("custom_groups") or []) if g not in (body.name, new_name)]
    if _scoped_group_query(db, mother_id, new_name).count() or new_name in customs or (mother.group or "") == new_name:
        raise HTTPException(409, f"分组「{new_name}」已存在")
    moved = _scoped_group_query(db, mother_id, body.name).update({Asset.group: new_name}, synchronize_session=False)
    # 母机自身卡（本机·母机）的分组存在母机资产上，一并迁移
    if (mother.group or "") == body.name:
        mother.group = new_name
        moved += 1
    mother.extra = {**(mother.extra or {}), "custom_groups": customs + [new_name]}
    db.commit()
    return {"ok": True, "moved": moved}


@router.post("/api/v1/assets/mothers/{mother_id}/groups/delete", dependencies=[Depends(require_perm("assets:write"))])
def delete_group(mother_id: str, body: GroupDeleteIn, db: Session = Depends(get_db)):
    mother = db.get(Asset, mother_id)
    if mother is None or mother.kind != "mother":
        raise HTTPException(404, "母机不存在")
    cnt = _scoped_group_query(db, mother_id, body.name).count()
    if (mother.group or "") == body.name:
        cnt += 1  # 母机自身卡也在该分组
    if cnt:
        raise HTTPException(409, f"分组「{body.name}」下还有 {cnt} 台成员，请先移出后再删除")
    customs = [g for g in ((mother.extra or {}).get("custom_groups") or []) if g != body.name]
    mother.extra = {**(mother.extra or {}), "custom_groups": customs}
    db.commit()
    return {"ok": True}


@router.get("/api/v1/assets/mothers/{mother_id}/groups", dependencies=[Depends(require_perm("assets:read"))])
def mother_group_options(mother_id: str, db: Session = Depends(get_db)):
    """母机的分组选项（含自定义空分组与母机自身卡分组），供任务单页筛选切换。"""
    mother = db.get(Asset, mother_id)
    if mother is None or mother.kind != "mother":
        raise HTTPException(404, "母机不存在")
    counts: dict[str, int] = {}
    for a in db.scalars(
        select(Asset)
        .filter(Asset.kind != "mother", or_(Asset.mother_id == mother_id, Asset.mother_id.is_(None)))
    ):
        counts[a.group or ""] = counts.get(a.group or "", 0) + 1
    # 母机自身卡分组
    counts[mother.group or ""] = counts.get(mother.group or "", 0) + 1
    for g in (mother.extra or {}).get("custom_groups") or []:
        counts.setdefault(g, 0)
    items = [{"name": k, "total": v} for k, v in sorted(counts.items(), key=lambda kv: (kv[0] == "", kv[0]))]
    return {"items": items}


class AssetUpdateIn(BaseModel):
    """资产编辑：基础信息 + 业务分组（group 传空串表示移出分组）。

    custom_groups 用于维护"空分组"列表（还没有子机的分组也保留展示）。
    """

    hostname: str = Field(default="", max_length=128)
    app: str = Field(default="", max_length=64)
    role: str = Field(default="", max_length=64)
    env: str = Field(default="", max_length=32)
    owner: str = Field(default="", max_length=64)
    group: str | None = Field(default=None, max_length=64)
    custom_groups: list[str] | None = None


@router.patch("/api/v1/assets/{asset_id}", dependencies=[Depends(require_perm("assets:write"))])
def update_asset(asset_id: str, body: AssetUpdateIn, db: Session = Depends(get_db)):
    """编辑资产基础信息与业务分组。仅更新显式传入且非空的字段。"""
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    if body.hostname:
        exists = db.query(Asset).filter(Asset.hostname == body.hostname, Asset.id != asset_id).first()
        if exists:
            raise HTTPException(409, f"主机名已被资产 {exists.id} 占用")
        a.hostname = body.hostname
    if body.app:
        a.app = body.app
    if body.role:
        a.role = body.role
    if body.env:
        a.env = body.env
    if body.owner:
        a.owner = body.owner
    if body.group is not None:
        a.group = body.group.strip()
    if body.custom_groups is not None:
        names: list[str] = []
        for g in body.custom_groups[:32]:
            g = g.strip()[:64]
            if g and g not in names:
                names.append(g)
        a.extra = {**(a.extra or {}), "custom_groups": names}
    db.commit()
    return _asset_row(a)


@router.get("/api/v1/assets/{asset_id}", dependencies=[Depends(require_perm("assets:read"))])
def get_asset(asset_id: str, db: Session = Depends(get_db)):
    """资产详情：全字段 + 纳管进度 + 关联数量。"""
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    row = _asset_row(a)
    extra = dict(a.extra or {})
    # netdata 的 token 类字段脱敏后返回（zabbix 段已随栈卸载移除）
    ncfg = dict(extra.get("netdata") or {})
    for secret_key in ("token", "bearer_token"):
        if ncfg.get(secret_key):
            ncfg[secret_key] = "******"
    if ncfg:
        extra["netdata"] = ncfg
    row["extra"] = extra
    row["tickets"] = db.query(Ticket).filter(Ticket.asset_id == asset_id).count()
    row["maintenance_windows"] = db.query(MaintenanceWindow).filter(MaintenanceWindow.asset_id == asset_id).count()
    row["backup_jobs"] = db.query(BackupJob).filter(BackupJob.asset_id == asset_id).count()
    return row


def _mother_children(db: Session, mother_id: str) -> list[Asset]:
    """母机名下的全部子机台账（kind != mother，含 node/child 等一切非母机节点）。"""
    return db.scalars(select(Asset).where(Asset.kind != "mother", Asset.mother_id == mother_id)).all()


def _assert_refs_free(db: Session, assets: list[Asset]) -> None:
    """工单/维护窗口/备份任务引用检查：任一被引用即 409，保证级联不产生部分删除。"""
    ids = [a.id for a in assets]
    refs = {
        "tickets": db.query(Ticket).filter(Ticket.asset_id.in_(ids)).count(),
        "maintenance_windows": db.query(MaintenanceWindow).filter(MaintenanceWindow.asset_id.in_(ids)).count(),
        "backup_jobs": db.query(BackupJob).filter(BackupJob.asset_id.in_(ids)).count(),
    }
    if any(refs.values()):
        raise HTTPException(409, f"资产被引用，先处理关联数据: {refs}")


@router.delete("/api/v1/assets/{asset_id}", dependencies=[Depends(require_perm("assets:write"))])
def delete_asset(asset_id: str, db: Session = Depends(get_db), current: CurrentUser = Depends(require_perm("assets:write"))):
    """删除资产台账；母机名下仍有子机时拒绝（必须先删除全部子机）。"""
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    if a.kind == "mother":
        children = _mother_children(db, asset_id)
        if children:
            raise HTTPException(
                400,
                f"该母机名下仍有 {len(children)} 台子机，请先删除全部子机后再删除母机",
            )
    _assert_refs_free(db, [a])
    db.delete(a)
    add_audit(
        db,
        ticket_id=None,
        event_type="asset_delete",
        actor=current.username,
        result={"asset_id": asset_id, "hostname": a.hostname},
    )
    db.commit()
    return {"deleted": asset_id, "cascade_children": []}


class AssetRemoveIn(BaseModel):
    """删除子机：可选先 SSH 卸载远端自研 agent（安装密码不落库）。"""

    uninstall: bool = False
    ssh_password: str = Field(default="", max_length=128)


@router.post("/api/v1/assets/{asset_id}/remove")
def remove_asset(
    asset_id: str,
    body: AssetRemoveIn,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """删除子机资产；默认先 SSH 到来源机停止并清理自研 agent（go/py）。

    母机名下仍有子机时拒绝删除（必须先删除全部子机）。
    """
    from app.services import agent_deployer

    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    if a.kind == "mother":
        children = _mother_children(db, asset_id)
        if children:
            raise HTTPException(
                400,
                f"该母机名下仍有 {len(children)} 台子机，请先删除全部子机后再删除母机",
            )
        if body.uninstall:
            raise HTTPException(400, "母机删除为纯台账操作（需先删净子机），不支持卸载")
    _assert_refs_free(db, [a])

    # 1) 远程卸载自研 agent（纳管来源机；密码仅本次使用）
    uninstall_logs: list[str] = []
    prov = (a.extra or {}).get("provision") or {}
    if body.uninstall:
        ip = str(prov.get("ip") or "").strip()
        port = int(prov.get("port") or 22)
        username = str(prov.get("username") or "root")
        if not ip:
            raise HTTPException(400, "该资产没有纳管来源（ip/端口），无法远程卸载；请取消勾选「卸载 agent」后直接删除")
        if not body.ssh_password:
            raise HTTPException(400, "请输入该服务器的 SSH 密码用于卸载 agent")
        try:
            ssh = provision_svc._connect_ssh(ip, port, username, body.ssh_password)
            try:
                agent_deployer.uninstall_via_ssh(ssh, logs=uninstall_logs)
            finally:
                ssh.close()
        except provision_svc.ProvisionError as exc:
            tail = "；".join(uninstall_logs[-2:])
            raise HTTPException(502, f"远程卸载失败：{exc}" + (f"（最近日志：{tail}）" if tail else ""))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"SSH 连接失败（{ip}:{port}）：{exc}")

    db.delete(a)
    add_audit(
        db,
        ticket_id=None,
        event_type="asset_remove",
        actor=current.username,
        result={
            "asset_id": asset_id,
            "hostname": a.hostname,
            "uninstalled": body.uninstall,
            "uninstall_logs": uninstall_logs[-8:],
        },
    )
    db.commit()
    return {
        "deleted": asset_id,
        "uninstalled": body.uninstall,
        "uninstall_logs": uninstall_logs[-8:],
        "cascade_children": [],
    }


@router.get("/api/v1/assets/{asset_id}/provision", dependencies=[Depends(require_perm("assets:read"))])
def get_asset_provision(asset_id: str, db: Session = Depends(get_db)):
    """纳管详情：安装状态、完整安装日志与安装信息（Agent 版本/安装位置/运行方式）。"""
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    prov = (a.extra or {}).get("provision") or {}
    dep = (a.extra or {}).get("agent_deploy") or {}
    return {
        "asset_id": asset_id,
        "status": prov.get("status", ""),
        "ip": prov.get("ip", ""),
        "port": prov.get("port", 22),
        "username": prov.get("username", ""),
        "started_at": prov.get("started_at", ""),
        "finished_at": prov.get("finished_at", ""),
        "error": prov.get("error") or dep.get("error", ""),
        "install_info": prov.get("install_info") or None,
        "lang": dep.get("lang", ""),
        "pid": dep.get("pid", ""),
        "deploy_state": dep.get("state", ""),
        "logs": prov.get("logs") or dep.get("steps", []),
    }


class AssetMetricsOut(BaseModel):
    mapped: bool
    real: bool
    asset_id: str
    minutes: int
    latest: dict
    series: dict
    note: str = ""


@router.get("/api/v1/assets/{asset_id}/metrics", dependencies=[Depends(require_perm("assets:read"))])
def asset_metrics(asset_id: str, db: Session = Depends(get_db), minutes: int = Query(default=60, ge=5, le=1440)) -> AssetMetricsOut:
    """资产监控大盘：CPU/内存/磁盘/负载 折线。

    数据源：本地落库的 5 分钟趋势样本（子机来自 agent 上报，母机来自本机 /proc）；
    本地无样本时（未接入 agent 且非母机）回落 mock 演示序列。
    """
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    stored = query_series(db, asset_id, minutes / 60.0)
    if stored["count"]:
        latest = {k: (s[-1]["v"] if s else None) for k, s in stored["series"].items()}
        return AssetMetricsOut(
            mapped=True,
            real=True,
            asset_id=asset_id,
            minutes=minutes,
            latest=latest,
            series=stored["series"],
        )
    mock = _mock_asset_metrics(asset_id, minutes)
    return AssetMetricsOut(mapped=True, real=False, asset_id=asset_id, minutes=minutes,
                           latest=mock["latest"], series=mock["series"],
                           note="暂无监控样本（子机未部署 agent），展示演示数据")


class AssetTrendsOut(BaseModel):
    asset_id: str
    hours: float
    # stored 本地落库样本为主 / realtime 本地无样本回退实时（或演示）序列
    source: str
    latest: dict
    series: dict
    compare: list = []
    baseline_bands: dict = {}
    anomalies: list = []
    forecast: dict = {}
    note: str = ""


def _norm_series(points: list | None, key: str) -> list[dict]:
    """归一化序列点为 {t: unix秒, v: float}（兼容 real 的 {t, cpu} 与 mock 的 {t, v} 形式）。"""
    out = []
    for p in points or []:
        try:
            t = int(p.get("t"))
        except (TypeError, ValueError):
            continue
        v = p.get(key, p.get("v"))
        if v is None:
            continue
        out.append({"t": t, "v": round(float(v), 2)})
    return out


def _merge_series(stored: dict, realtime: dict) -> dict:
    """落库序列与实时序列合并：实时点（1 分钟精度）优先覆盖同一时刻的落库样本。"""
    out: dict[str, list[dict]] = {}
    for key in ("cpu", "mem", "disk", "load"):
        pts: dict[int, dict] = {p["t"]: p for p in _norm_series(stored.get(key), key)}
        for p in _norm_series(realtime.get(key), key):
            pts[p["t"]] = p
        out[key] = sorted(pts.values(), key=lambda p: p["t"])
    return out


@router.get("/api/v1/assets/{asset_id}/trends", dependencies=[Depends(require_perm("assets:read"))])
def asset_trends(
    asset_id: str,
    db: Session = Depends(get_db),
    hours: float = Query(default=6, ge=0.5, le=24),
    compare_days: int = Query(default=0, ge=0, le=7),
    with_baseline: bool = Query(default=True),
    with_forecast: bool = Query(default=False),
) -> AssetTrendsOut:
    """资产趋势分析：本地落库指标（5 分钟粒度）+ 实时序列补尾。

    - hours：时间范围，最大 24 小时
    - compare_days：返回前 N 天同长度窗口序列（平移对齐到当前时间轴，多日同轴叠加对比）
    - with_baseline：返回历史基线带（P05~P95，按日内时段）与显著偏离的异常点
    - with_forecast：返回各指标线性回归外推的未来趋势点
    """
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")

    stored = query_series(db, asset_id, hours)
    realtime: dict = {}
    # 实时补尾：优先子机 agent 最新一帧（单点）；mock 模式下无落库样本时回演示序列
    from app.routers.agent_api import LATEST, _LATEST_LOCK

    with _LATEST_LOCK:
        frame = LATEST.get(asset_id)
    if frame:
        t = int(utcnow().timestamp())
        realtime = {
            k: [{"t": t, "v": round(float(frame[k]), 2)}]
            for k in ("cpu", "mem", "disk")
            if frame.get(k) is not None
        }
        if frame.get("load1") is not None:
            realtime["load"] = [{"t": int(utcnow().timestamp()), "v": round(float(frame["load1"]), 2)}]
    elif stored["count"] == 0 and get_settings().integration_mode == "mock":
        realtime = _mock_asset_metrics(asset_id, min(int(hours * 60), 1440))["series"]

    series = _merge_series(stored["series"], realtime)
    latest = {k: (s[-1]["v"] if s else None) for k, s in series.items()}
    compare = query_compare(db, asset_id, compare_days, hours) if compare_days > 0 else []

    bands: dict = {}
    anomalies: list = []
    if with_baseline:
        baseline = build_baseline(db, asset_id)
        now_unix = int(utcnow().timestamp())
        bands = baseline_bands(baseline, now_unix - int(hours * 3600), now_unix)
        anomalies = detect_anomalies(series, baseline)

    forecast: dict = {}
    if with_forecast:
        forecast = {k: forecast_series(v) for k, v in series.items() if len(v) >= 5}

    source = "realtime" if stored["count"] == 0 and any(series.values()) else "stored"
    notes = []
    if not stored["count"]:
        notes.append("本地暂无落库样本（采集任务每 5 分钟落库），展示实时/演示序列")
    return AssetTrendsOut(
        asset_id=asset_id,
        hours=hours,
        source=source,
        latest=latest,
        series=series,
        compare=compare,
        baseline_bands=bands,
        anomalies=anomalies,
        forecast=forecast,
        note="；".join(notes),
    )


class AssetInspectIn(BaseModel):
    username: str = ""
    password: str = Field(default="", max_length=128)
    port: int = Field(default=22, ge=1, le=65535)


def _ssh_target_for_asset(a: Asset, body: AssetInspectIn) -> tuple[str, int, str]:
    """解析资产的 SSH 目标（IP/端口/用户名），与巡检、详情采集共用。无 IP 抛 400。"""
    extra = a.extra or {}
    provision = extra.get("provision") or {}
    # IP 来源：纳管登记 > 母机部署登记（母机不走纳管流程，但部署时登记过 SSH 信息）
    ip = provision.get("ip") or (extra.get("deploy") or {}).get("ip") or ""
    if not ip:
        raise HTTPException(400, "该资产未登记 IP，无法 SSH 巡检（自动纳管的节点支持此功能）")
    # 用户名：前端传入 > 巡检配置 > 纳管登记 > root
    username = body.username or (extra.get("inspect") or {}).get("username") or provision.get("username") or "root"
    port = body.port if body.port != 22 or not provision.get("port") else int(provision["port"])
    return ip, port, username


def _ssh_collect(fn, password_used: bool) -> dict:
    """SSH 上机采集公共封装：认证失败用 409（避免触发前端 401 全局登出踢人）。"""
    import paramiko

    try:
        return fn()
    except paramiko.AuthenticationException:
        if password_used:
            raise HTTPException(409, "SSH 认证失败：用户名或密码错误") from None
        raise HTTPException(
            409, "免密巡检未就绪：该机器尚未配置巡检公钥，请在纳管时自动下发或联系管理员"
        ) from None
    except FileNotFoundError:
        raise HTTPException(503, "巡检私钥未配置，无法免密采集") from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"SSH 连接失败：{exc}") from exc


@router.post("/api/v1/assets/{asset_id}/inspect", dependencies=[Depends(require_perm("assets:read"))])
def asset_inspect(asset_id: str, body: AssetInspectIn, db: Session = Depends(get_db)):
    """实时巡检：SSH 登录目标机采集 top 进程排行 / 内存 / 磁盘 / 负载。

    免密优先：前端不传凭据时自动使用内置巡检私钥 + 资产登记用户；传密码则密码即用即弃。
    """
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    ip, port, username = _ssh_target_for_asset(a, body)

    from app.services.inspector import INSPECT_KEY_PATH, inspect_host

    result = _ssh_collect(
        lambda: inspect_host(ip, port, username, body.password, key_path=INSPECT_KEY_PATH),
        password_used=bool(body.password),
    )
    result["asset_id"] = asset_id
    return result


@router.post("/api/v1/assets/{asset_id}/sysinfo", dependencies=[Depends(require_perm("assets:read"))])
def asset_sysinfo(asset_id: str, body: AssetInspectIn, db: Session = Depends(get_db)):
    """服务器详细信息：SSH 上机一次性采集 系统/硬件/内存/磁盘/网络/进程 全景（只读命令）。"""
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    ip, port, username = _ssh_target_for_asset(a, body)

    from app.services.inspector import INSPECT_KEY_PATH, collect_sysinfo

    result = _ssh_collect(
        lambda: collect_sysinfo(ip, port, username, body.password, key_path=INSPECT_KEY_PATH),
        password_used=bool(body.password),
    )
    result["asset_id"] = asset_id
    return result


class AssetUpsertIn(BaseModel):
    """登记/更新资产（供脚本自动纳管回写，id 建议用主机名）。"""

    id: str = Field(min_length=1, max_length=64)
    hostname: str = ""
    app: str = ""
    role: str = "app"
    env: str = "prod"
    owner: str = ""
    source: str = "add-node"


@router.post("/api/v1/assets/upsert", dependencies=[Depends(require_perm("assets:write"))])
def upsert_asset(body: AssetUpsertIn, db: Session = Depends(get_db)):
    settings = get_settings()
    asset = db.get(Asset, body.id)
    created = False
    if asset is None:
        asset = Asset(
            id=body.id,
            hostname=body.hostname or body.id,
            app=body.app,
            role=body.role,
            env=body.env,
            owner=body.owner,
            tenant_id=settings.tenant_id,
            reachable=True,
        )
        db.add(asset)
        created = True
    else:
        if body.hostname:
            asset.hostname = body.hostname
        if body.owner:
            asset.owner = body.owner
        asset.reachable = True
    extra = dict(asset.extra or {})
    extra["upsert"] = {
        **(extra.get("upsert") or {}),
        "source": body.source,
        "synced_at": utcnow().isoformat(),
    }
    asset.extra = extra
    add_audit(
        db,
        ticket_id=None,
        event_type="asset_upsert",
        actor=body.source,
        result={"asset_id": asset.id},
    )
    db.commit()
    return {
        "id": asset.id,
        "hostname": asset.hostname,
        "reachable": asset.reachable,
        "created": created,
        "synced_at": extra["upsert"]["synced_at"],
    }


def _spawn_agent_deploy(
    asset_id: str,
    *,
    ip: str,
    port: int,
    username: str,
    password: str,
    lang: str,
    token: str,
    server_url: str,
    cfg: dict,
    requested_by: str,
) -> None:
    """后台线程执行 agent 部署（SSH 下发约 1 分钟），失败落库避免状态停在「安装中」。"""
    from app.services import agent_deployer

    def _run_deploy() -> None:
        from app.database import SessionLocal

        db2 = SessionLocal()
        try:
            agent_deployer.deploy_to_host(
                db2,
                asset_id,
                ip=ip,
                lang=lang,
                ssh_user=username,
                ssh_password=password,
                token=token,
                server_url=server_url,
                cfg=cfg,
                port=port,
                requested_by=requested_by,
            )
        except Exception as exc:  # noqa: BLE001 失败落库，避免状态永远停在"安装中"
            asset2 = db2.get(Asset, asset_id)
            if asset2:
                prov = (asset2.extra or {}).get("provision") or {}
                asset2.extra = {
                    **(asset2.extra or {}),
                    "provision": {**prov, "status": "failed", "error": str(exc)[:500]},
                    "agent_deploy": {
                        **(asset2.extra or {}).get("agent_deploy", {}),
                        "state": "failed",
                        "error": str(exc)[:500],
                    },
                }
                db2.commit()
        finally:
            db2.close()

    threading.Thread(target=_run_deploy, daemon=True, name=f"provision-{asset_id}").start()


class AssetProvisionIn(BaseModel):
    """资产页一键纳管：SSH 密码直连新机下发自研 agent。密码仅本次部署使用，不落库。"""

    display_name: str = Field(default="", max_length=64, description="子机显示名；空=用安装后的真实主机名")
    ip: str = Field(min_length=3, max_length=64)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default="root", max_length=64)
    password: str = Field(min_length=1, max_length=128)
    lang: str = Field(default="py", pattern="^(py|go)$", description="agent 实现语言：py=Python（自动装环境），go=静态二进制")
    app: str = Field(default="", max_length=64)
    role: str = Field(default="app", max_length=64)
    env: str = Field(default="prod", max_length=32)
    owner: str = Field(default="", max_length=64)
    group: str = Field(default="", max_length=64)
    mother_id: str = Field(default="", max_length=64, description="归属母机；空=默认母机")


@router.post("/api/v1/assets/provision")
def provision_asset(
    body: AssetProvisionIn,
    request: Request,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """资产页一键纳管：SSH 登录新机部署自研 agent（采集并上报本机指标）。

    归属母机：显式指定 → 校验存在；未指定 → 默认母机（不存在则留空）。
    """
    import secrets as _secrets

    from app.routers.agent_api import agent_cfg_of

    # 归属母机
    requested_mother = body.mother_id.strip()
    mother_id = requested_mother or get_settings().mother_asset_id
    mother = db.get(Asset, mother_id)
    if requested_mother and mother is None:
        raise HTTPException(404, f"归属母机不存在：{mother_id}")
    if mother is not None and mother.kind != "mother":
        raise HTTPException(400, f"资产 {mother_id} 不是母机")
    mother_id = mother.id if mother is not None else ""
    # 唯一性按 (地址, 端口) 判定：同 IP 不同 SSH 端口是不同资产；
    # 已存在（兼容旧格式不含端口的 id）则复用原资产幂等重跑，避免重复建卡
    aid = ""
    for node in db.query(Asset).filter(Asset.id.like("node-%")).all():
        p = (node.extra or {}).get("provision") or {}
        if (
            str(p.get("ip") or "").strip().lower() == body.ip.strip().lower()
            and int(p.get("port") or 22) == int(body.port)
        ):
            aid = node.id
            break
    if not aid:
        aid = provision_svc.asset_id_for_ip(body.ip, body.port)
    asset = db.get(Asset, aid)
    if asset is None:
        disp_name = body.display_name.strip()
        if disp_name and db.query(Asset).filter(Asset.hostname == disp_name).first():
            raise HTTPException(409, f"子机名称已被资产占用：{disp_name}")
        asset = Asset(
            id=aid,
            # 占位展示名：优先用户填的子机名称，否则带端口的 ip:port（避免同 IP 不同端口撞唯一约束）；
            # 安装成功后未命名资产会被新机真实主机名覆盖（冲突时自动加端口后缀）
            hostname=disp_name or f"{body.ip.strip()}:{body.port}",
            app=body.app,
            role=body.role,
            env=body.env,
            owner=body.owner,
            group=body.group.strip(),
            kind="child",
            mother_id=mother_id,
            tenant_id=get_settings().tenant_id,
            reachable=False,
            extra={"provision": {"status": "running", "ip": body.ip, "port": body.port, "username": body.username}},
        )
    else:
        asset.role = body.role
        asset.env = body.env
        asset.mother_id = mother_id
        if body.app:
            asset.app = body.app
        if body.owner:
            asset.owner = body.owner
        if body.group.strip():
            asset.group = body.group.strip()
        extra = dict(asset.extra or {})
        extra["provision"] = {"status": "running", "ip": body.ip, "port": body.port, "username": body.username}
        asset.extra = extra
    # 签发 agent 令牌（上报认证用）
    token = _secrets.token_urlsafe(24)
    asset.extra = {**(asset.extra or {}), "agent_token": token}
    db.add(asset)
    db.commit()

    # 后台线程执行部署（SSH 下发约 1 分钟），接口立即返回
    server_url = (get_settings().platform_public_url or str(request.base_url).rstrip("/")).strip()
    cfg = agent_cfg_of(asset, db)
    _spawn_agent_deploy(
        aid,
        ip=body.ip,
        port=body.port,
        username=body.username,
        password=body.password,
        lang=body.lang,
        token=token,
        server_url=server_url,
        cfg=cfg,
        requested_by=current.username,
    )
    return {
        "id": aid,
        "status": "running",
        "server_url": server_url,
        "message": "开始纳管：正在连接新机部署自研 agent（约 1 分钟），资产列表将自动刷新",
    }


def _find_asset_for_alert(db: Session, body: ZabbixWebhookIn) -> Asset | None:
    """按 asset_id → hostname（大小写不敏感）→ external_id 解析告警归属资产。"""
    if (body.asset_id or "").strip():
        row = db.get(Asset, body.asset_id.strip())
        if row is not None:
            return row
    for name in (body.hostname, body.host):
        if (name or "").strip():
            row = db.scalar(select(Asset).where(func.lower(Asset.hostname) == name.strip().lower()))
            if row is not None:
                return row
    if (body.hostid or "").strip():
        return db.scalar(select(Asset).where(Asset.external_id == body.hostid.strip()))
    return None


def _check_webhook_secret(
    x_webhook_secret: str | None = Header(default=None),
    x_zabbix_token: str | None = Header(default=None, alias="X-Zabbix-Token"),
    authorization: str | None = Header(default=None),
) -> None:
    provided = x_webhook_secret or x_zabbix_token or ""
    # 仅当未提供专用密钥头时才回退到 Authorization: Bearer（显式密钥优先）
    if not provided and authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(" ", 1)[1]
    expected = get_settings().webhook_secret
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(401, "Webhook 鉴权失败")


@router.post("/api/v1/webhooks/zabbix")
def zabbix_webhook(
    body: ZabbixWebhookIn,
    db: Session = Depends(get_db),
    _: None = Depends(_check_webhook_secret),
):
    """告警入口（兼容原 Zabbix webhook 路径与字段）：记录「异常/恢复」两态条目（按 event_id 幂等更新）。

    默认不自动立案；WEBHOOK_AUTO_TICKET=true 时兼容旧的自动立案管线。
    """
    recovered = str(body.value or "").upper() in {"OK", "RESOLVED"}
    asset = _find_asset_for_alert(db, body)

    now = utcnow()
    anomaly = db.scalar(select(AnomalyEvent).where(AnomalyEvent.event_id == body.event_id))
    duplicate = anomaly is not None
    if anomaly is None:
        anomaly = AnomalyEvent(event_id=body.event_id, first_seen_at=now)
        db.add(anomaly)
    anomaly.hostid = body.hostid or ""
    anomaly.host = body.host or ""
    anomaly.hostname = body.hostname or ""
    anomaly.ip = body.ip or ""
    anomaly.trigger_name = body.trigger_name or ""
    anomaly.severity = body.severity or "high"
    anomaly.message = (body.message or body.trigger_name or "")[:250]
    anomaly.asset_id = asset.id if asset is not None else None
    anomaly.payload = body.model_dump(mode="json")
    anomaly.last_seen_at = now
    anomaly.status = "recovered" if recovered else "abnormal"
    anomaly.recovered_at = now if recovered else None
    db.flush()
    # 每条通知都留痕：保留完整原始载荷（异常/恢复均记录），用于回溯（如夜间短时异常）
    db.add(
        AnomalyLog(
            anomaly_id=anomaly.id,
            event_id=body.event_id,
            action="recovered" if recovered else "problem",
            payload=body.model_dump(mode="json"),
            received_at=now,
        )
    )
    db.commit()
    db.refresh(anomaly)

    # 首次出现的异常：自动 SSH 采集异常时刻进程快照（生产后台线程；测试同步，避免共享连接事务交错）
    if not recovered and not duplicate:
        if get_settings().diagnostics_async:
            threading.Thread(target=diagnostics_svc.collect_for_anomaly, args=(anomaly.id,), daemon=True).start()
        else:
            diagnostics_svc.collect_for_anomaly(anomaly.id)

    # 告警 → AI 日志分析联动（仅首次异常；级别映射优先级；未启用则跳过；失败不影响 webhook 应答）
    ai_dispatched = False
    if not recovered and not duplicate:
        try:
            from app.services.ai_analyzer import get_ai_config, priority_for
            from app.workers.tasks import analyze_anomaly_logs

            cfg = get_ai_config(db)
            if cfg.get("enabled") and anomaly.ai_status not in ("pending", "running", "done"):
                anomaly.ai_status = "pending"
                db.add(AiAuditLog(anomaly_id=anomaly.id, action="trigger", operator="webhook",
                                  ok=True, detail={"source": "webhook"}))
                db.commit()
                if get_settings().use_celery:
                    analyze_anomaly_logs.apply_async(args=[anomaly.id], priority=priority_for(anomaly))
                else:
                    analyze_anomaly_logs.run(anomaly.id)
                ai_dispatched = True
        except Exception:
            db.rollback()
            log.exception("AI 分析联动派发失败 anomaly_id=%s", anomaly.id)

    # 告警 → 恢复任务联动：首次异常生成任务（命中脚本低风险自动执行），恢复自动关闭
    recovery_created = False
    try:
        from app.services.recovery import ensure_for_anomaly, resolve_for_anomaly

        if not recovered:
            if ensure_for_anomaly(db, anomaly) is not None:
                recovery_created = True
        else:
            resolve_for_anomaly(db, anomaly.event_id)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("恢复任务联动失败 event_id=%s", anomaly.event_id)

    ticket_out = None
    skipped = False
    if get_settings().webhook_auto_ticket and not recovered and asset is not None:
        result = _auto_ticket_from_webhook(body, db, asset)
        ticket_out = result.get("ticket")
        skipped = bool(result.get("skipped"))

    return {
        "status": anomaly.status,
        "duplicate": duplicate,
        "skipped": skipped,
        "ai_dispatched": ai_dispatched,
        "anomaly": AnomalyOut.model_validate(anomaly),
        "ticket": ticket_out,
    }


def _auto_ticket_from_webhook(body: ZabbixWebhookIn, db: Session, asset: Asset) -> dict:
    """旧自动立案管线（仅 WEBHOOK_AUTO_TICKET=true 时走这里）。"""
    if asset.tenant_id != get_settings().tenant_id:
        raise HTTPException(403, "拒绝跨租户目标")

    key = make_idempotency_key(body.event_id, asset.id, body.job_version, body.action_type)
    existing = db.scalar(select(Ticket).where(Ticket.idempotency_key == key))
    if existing is not None:
        add_event(db, ticket_id=existing.id, kind="dedup_merged", message=f"重复告警已合并 event_id={body.event_id}")
        add_audit(db, ticket_id=existing.id, event_type="dedup", result={"event_id": body.event_id, "key": key})
        db.commit()
        return {"duplicate": True, "skipped": False, "ticket": TicketOut.model_validate(existing)}

    skipped = in_maintenance(db, asset.id)
    scenario = body.demo_scenario or _infer_scenario(body.trigger_name)

    alert = AlertEvent(
        idempotency_key=key,
        event_id=body.event_id,
        asset_id=asset.id,
        payload=body.model_dump(),
        skipped=bool(skipped),
        skip_reason="maintenance_window" if skipped else None,
    )
    db.add(alert)
    db.flush()

    if skipped:
        add_audit(
            db,
            ticket_id=None,
            event_type="maintenance_skip",
            result={"asset_id": asset.id, "reason": skipped.reason, "event_id": body.event_id},
        )
        db.commit()
        return {"duplicate": False, "skipped": True, "ticket": None}

    ticket = Ticket(
        number=next_ticket_number(db),
        idempotency_key=key,
        source="alert",
        status=TicketStatus.pending_analysis.value,
        employee_id=get_settings().employee_id,
        asset_id=asset.id,
        tenant_id=asset.tenant_id,
        title=body.message or body.trigger_name,
        trigger_name=body.trigger_name,
        severity=body.severity,
        action_type=body.action_type,
        job_version=body.job_version,
        event_id=body.event_id,
        owner=asset.owner,
        demo_scenario=scenario,
    )
    db.add(ticket)
    db.flush()
    alert.ticket_id = ticket.id
    add_event(db, ticket_id=ticket.id, kind="alert_received", message=f"告警立案 {ticket.number}")
    add_audit(
        db,
        ticket_id=ticket.id,
        event_type="ticket_created",
        result={"number": ticket.number, "asset_id": asset.id},
    )
    notify_ticket(db, ticket, "ticket_created", f"{ticket.number} 已立案，正在排查")
    db.commit()
    dispatch_investigation(ticket.id)
    db.refresh(ticket)
    return {"duplicate": False, "skipped": False, "ticket": TicketOut.model_validate(ticket)}


def _infer_scenario(trigger: str) -> str:
    t = trigger.lower()
    if "replication" in t or "主备" in t or "failover" in t:
        return "yellow"
    if "未知" in t or "mystery" in t or "native" in t:
        return "red"
    if "verify" in t or "探测失败" in t:
        return "verify_fail"
    return "green"


SEVERITY_LABELS = {
    "P0": "P0 严重故障",
    "P1": "P1 重要告警",
    "P2": "P2 一般告警",
    "P3": "P3 提示信息",
    # 历史（Zabbix 时代）级别值，仅兼容展示
    "disaster": "灾难",
    "high": "严重",
    "average": "较严重",
    "warning": "警告",
    "information": "提示",
    "not_classified": "未知",
}


def _attach_asset_hierarchy(db: Session, rows: list[AnomalyEvent]) -> list[dict]:
    """给异常条目补充 母机/组/子机 三级归属信息（按 asset_id 关联资产台账）。"""
    asset_ids = {r.asset_id for r in rows if r.asset_id}
    assets = {a.id: a for a in db.query(Asset).filter(Asset.id.in_(asset_ids)).all()} if asset_ids else {}
    mother_ids = {a.mother_id for a in assets.values() if a.mother_id}
    mothers = {m.id: m for m in db.query(Asset).filter(Asset.id.in_(mother_ids)).all()} if mother_ids else {}

    items: list[dict] = []
    for r in rows:
        d = AnomalyOut.model_validate(r).model_dump()
        asset = assets.get(r.asset_id)
        if asset is not None and asset.kind == "mother":
            # 母机自身 agent 上报：母机自身也是一台子机（本机·母机）
            d["mother_id"] = asset.id
            d["mother_name"] = asset.hostname or asset.id
            d["group"] = asset.group or ""
            d["child"] = asset.hostname or asset.id
            d["is_mother_self"] = True
        else:
            mother = mothers.get(asset.mother_id) if asset else None
            d["mother_id"] = mother.id if mother else None
            d["mother_name"] = mother.hostname if mother else "未归属"
            d["group"] = (asset.group or "") if asset else ""
            d["child"] = asset.hostname if asset else (r.hostname or r.host or "未知")
            d["is_mother_self"] = False
        items.append(d)
    return items


@router.get("/api/v1/anomalies", dependencies=[Depends(require_perm("anomalies:read"))])
def list_anomalies(
    status: str | None = None,
    severity: str | None = None,
    mother_id: str | None = None,
    asset_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """异常条目列表：仅 异常(abnormal)/恢复(recovered) 两态，分页 + 母机/组/子机归属。

    asset_id：精确过滤某台资产（子机或母机自身）的异常，供资产监控详情展示。
    """
    q = db.query(AnomalyEvent)
    if status in {"abnormal", "recovered"}:
        q = q.filter(AnomalyEvent.status == status)
    if severity:
        q = q.filter(AnomalyEvent.severity == severity)
    if asset_id:
        q = q.filter(AnomalyEvent.asset_id == asset_id)
    if mother_id:
        # 该母机下的子机 + 母机自身（mother_self）
        child_ids = [a.id for a in db.query(Asset).filter(Asset.mother_id == mother_id).all()]
        child_ids.append(mother_id)
        q = q.filter(AnomalyEvent.asset_id.in_(child_ids))

    total = q.count()
    rows = (
        q.order_by(AnomalyEvent.last_seen_at.desc(), AnomalyEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    abnormal_count = (
        db.scalar(select(func.count()).select_from(AnomalyEvent).where(AnomalyEvent.status == "abnormal")) or 0
    )
    return {
        "items": _attach_asset_hierarchy(db, rows),
        "abnormal_count": int(abnormal_count),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/api/v1/anomalies/stats", dependencies=[Depends(require_perm("anomalies:read"))])
def anomalies_stats(db: Session = Depends(get_db)):
    """异常统计：核心 KPI（当前未恢复/今日/本月/平均恢复时长）+ 分布与近 7 天趋势。"""
    now = datetime.now(BEIJING_TZ)
    today = now.date()

    def day_of(dt: datetime | None) -> date | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BEIJING_TZ).date()

    rows = list(db.scalars(select(AnomalyEvent)).all())

    asset_ids = {r.asset_id for r in rows if r.asset_id}
    assets = {a.id: a for a in db.query(Asset).filter(Asset.id.in_(asset_ids)).all()} if asset_ids else {}
    mother_ids = {a.mother_id for a in assets.values() if a.mother_id}
    mothers = {m.id: m for m in db.query(Asset).filter(Asset.id.in_(mother_ids)).all()} if mother_ids else {}

    current_abnormal = 0
    today_abnormal = 0
    month_abnormal = 0
    today_recovered = 0
    recover_seconds: list[float] = []
    severity_count: dict[str, int] = {}
    mother_count: dict[str, int] = {}
    trend: dict[str, dict[str, int]] = {}

    for r in rows:
        first_day = day_of(r.first_seen_at)
        rec_day = day_of(r.recovered_at)
        if r.status == "abnormal":
            current_abnormal += 1
        if first_day == today:
            today_abnormal += 1
        if first_day and first_day.month == today.month and first_day.year == today.year:
            month_abnormal += 1
        if r.status == "recovered" and rec_day == today:
            today_recovered += 1
        if r.recovered_at and r.first_seen_at:
            recover_seconds.append(max(0.0, (r.recovered_at - r.first_seen_at).total_seconds()))
        severity_count[r.severity] = severity_count.get(r.severity, 0) + 1
        asset = assets.get(r.asset_id)
        mother = mothers.get(asset.mother_id) if asset else None
        mkey = mother.hostname if mother else "未归属"
        mother_count[mkey] = mother_count.get(mkey, 0) + 1
        if first_day:
            t = trend.setdefault(first_day.isoformat(), {"abnormal": 0, "recovered": 0})
            t["abnormal"] += 1
            if r.status == "recovered" and rec_day:
                t2 = trend.setdefault(rec_day.isoformat(), {"abnormal": 0, "recovered": 0})
                t2["recovered"] += 1

    trend_7d = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        t = trend.get(d.isoformat(), {"abnormal": 0, "recovered": 0})
        trend_7d.append({"date": d.isoformat(), "abnormal": t["abnormal"], "recovered": t["recovered"]})

    severity_dist = [
        {"severity": s, "label": SEVERITY_LABELS.get(s, s), "count": c}
        for s, c in sorted(severity_count.items(), key=lambda kv: -kv[1])
    ]
    mother_dist = [
        {"mother": k, "count": v} for k, v in sorted(mother_count.items(), key=lambda kv: -kv[1])
    ]

    return {
        "current_abnormal": current_abnormal,
        "today_abnormal": today_abnormal,
        "month_abnormal": month_abnormal,
        "today_recovered": today_recovered,
        "avg_recover_minutes": round(sum(recover_seconds) / len(recover_seconds) / 60, 1) if recover_seconds else 0,
        "severity_dist": severity_dist,
        "mother_dist": mother_dist,
        "trend_7d": trend_7d,
    }


@router.get("/api/v1/anomalies/{aid}", dependencies=[Depends(require_perm("anomalies:read"))])
def anomaly_detail(aid: int, db: Session = Depends(get_db)):
    """异常详情：全部告警字段 + webhook 原始载荷 + 异常时刻进程快照 + 通知留痕。"""
    anomaly = db.get(AnomalyEvent, aid)
    if anomaly is None:
        raise HTTPException(404, "异常条目不存在")

    logs = (
        db.query(AnomalyLog)
        .filter(AnomalyLog.anomaly_id == aid)
        .order_by(AnomalyLog.id.desc())
        .limit(50)
        .all()
    )
    return {
        **AnomalyOut.model_validate(anomaly).model_dump(),
        "payload": anomaly.payload or {},
        "logs": [
            {
                "id": lg.id,
                "action": lg.action,
                "event_id": lg.event_id,
                "received_at": lg.received_at.isoformat() if lg.received_at else None,
                "payload": lg.payload or {},
            }
            for lg in logs
        ],
        "diagnostics": anomaly.diagnostics or {},
        "diag_status": anomaly.diag_status,
        "diag_at": anomaly.diag_at.isoformat() if anomaly.diag_at else None,
        "diag_error": anomaly.diag_error,
    }


@router.post("/api/v1/anomalies/{aid}/snapshot", dependencies=[Depends(require_perm("anomalies:read"))])
def anomaly_snapshot(aid: int, db: Session = Depends(get_db)):
    """手动触发/重试异常时刻进程快照采集（后台线程执行，前端轮询详情查看进度）。"""
    anomaly = db.get(AnomalyEvent, aid)
    if anomaly is None:
        raise HTTPException(404, "异常条目不存在")
    if anomaly.diag_status == "running":
        return {"status": "running", "message": "采集中，请稍候"}
    # 状态由采集线程自行置为 running（接口预置会与线程的 running 检查冲突）
    if get_settings().diagnostics_async:
        threading.Thread(target=diagnostics_svc.collect_for_anomaly, args=(aid,), daemon=True).start()
    else:
        diagnostics_svc.collect_for_anomaly(aid)
    return {"status": "running", "message": "已开始采集，请稍候刷新查看"}


@router.get("/api/v1/tickets", dependencies=[Depends(require_perm("tickets:read"))])
def list_tickets(
    status: str | None = None,
    mother_id: str | None = None,
    group: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    """任务单分页查询：支持按母机/分组/关键字（编号/标题/资产）联动资产台账筛选。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = db.query(Ticket)
    if mother_id or group:
        q = q.join(Asset, Ticket.asset_id == Asset.id)
        if mother_id:
            # 该母机名下子机 + 母机自身（本机·母机的告警挂在 mother 资产上）
            q = q.filter(or_(Asset.mother_id == mother_id, Asset.id == mother_id))
        if group:
            # 母机自身卡的分组同样写在 mother 资产的 group 列上
            q = q.filter(Asset.group == group)
    if status:
        q = q.filter(Ticket.status == status)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.filter(or_(Ticket.number.ilike(kw), Ticket.title.ilike(kw), Ticket.asset_id.ilike(kw)))
    total = q.count()
    rows = q.order_by(Ticket.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    # 批量补齐资产维度信息（子机 → 分组 → 母机），任务单与资产台账打通
    asset_ids = {t.asset_id for t in rows if t.asset_id}
    assets = {a.id: a for a in db.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all()} if asset_ids else {}
    mother_ids = {a.mother_id for a in assets.values() if a.mother_id}
    mothers = {m.id: m for m in db.scalars(select(Asset).where(Asset.id.in_(mother_ids))).all()} if mother_ids else {}
    items = []
    for t in rows:
        a = assets.get(t.asset_id)
        m = mothers.get(a.mother_id) if a else None
        info = {
            "hostname": a.hostname if a else None,
            "group": (a.group or "") if a else "",
            "mother_id": a.mother_id if a else None,
            "mother_hostname": m.hostname if m else (a.hostname if a and a.kind == "mother" else None),
        }
        items.append({**TicketOut.model_validate(t).model_dump(), "asset_info": info})
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/api/v1/tickets/{ticket_id}", dependencies=[Depends(require_perm("tickets:read"))])
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.scalar(
        select(Ticket).options(selectinload(Ticket.approvals)).where(Ticket.id == ticket_id)
    )
    if ticket is None:
        raise HTTPException(404, "任务单不存在")
    events = db.scalars(
        select(TicketEvent).where(TicketEvent.ticket_id == ticket_id).order_by(TicketEvent.id.asc())
    ).all()
    audits = db.scalars(
        select(AuditLog).where(AuditLog.ticket_id == ticket_id).order_by(AuditLog.id.asc())
    ).all()
    from app.services.locks import get_lock

    lock = get_lock(db, ticket.asset_id)
    notes = db.scalars(
        select(Notification).where(Notification.ticket_id == ticket_id).order_by(Notification.id.asc())
    ).all()
    return {
        "ticket": TicketOut.model_validate(ticket),
        "events": [TicketEventOut.model_validate(e) for e in events],
        "approvals": [
            {
                "id": a.id,
                "status": a.status,
                "asset_id": a.asset_id,
                "playbook_id": a.playbook_id,
                "playbook_version": a.playbook_version,
                "params_digest": a.params_digest,
                "approver": a.approver,
                "comment": a.comment,
                "expires_at": a.expires_at,
            }
            for a in ticket.approvals
        ],
        "audit": [
            {
                "id": a.id,
                "event_type": a.event_type,
                "actor": a.actor,
                "model_version": a.model_version,
                "policy_version": a.policy_version,
                "playbook_version": a.playbook_version,
                "approver": a.approver,
                "params_digest": a.params_digest,
                "evidence_refs": a.evidence_refs,
                "result": a.result,
                "created_at": a.created_at,
            }
            for a in audits
        ],
        "lock": None
        if lock is None
        else {
            "asset_id": lock.asset_id,
            "ticket_id": lock.ticket_id,
            "holder": lock.holder,
            "expires_at": lock.expires_at,
            "heartbeat_at": lock.heartbeat_at,
        },
        "notifications": [
            {
                "id": n.id,
                "kind": n.kind,
                "channel": n.channel,
                "title": n.title,
                "body": n.body,
                "read": n.read,
                "created_at": n.created_at,
            }
            for n in notes
        ],
    }


@router.post("/api/v1/tickets/{ticket_id}/approve", dependencies=[Depends(require_perm("tickets:operate"))])
def api_approve(
    ticket_id: int,
    body: ApprovalIn,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require_perm("tickets:operate")),
):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "任务单不存在")
    try:
        approve_ticket(db, ticket, body.approver or current.display_name, body.comment)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.refresh(ticket)
    return {"ticket": TicketOut.model_validate(ticket)}


@router.post("/api/v1/tickets/{ticket_id}/reject", dependencies=[Depends(require_perm("tickets:operate"))])
def api_reject(
    ticket_id: int,
    body: ApprovalIn,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require_perm("tickets:operate")),
):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "任务单不存在")
    try:
        reject_ticket(db, ticket, body.approver or current.display_name, body.comment)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.refresh(ticket)
    return {"ticket": TicketOut.model_validate(ticket)}


@router.get("/api/v1/audit", dependencies=[Depends(require_perm("audit:read"))])
def list_audit(ticket_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(200)
    if ticket_id is not None:
        stmt = select(AuditLog).where(AuditLog.ticket_id == ticket_id).order_by(AuditLog.id.asc())
    rows = db.scalars(stmt).all()
    return {
        "items": [
            {
                "id": a.id,
                "ticket_id": a.ticket_id,
                "event_type": a.event_type,
                "actor": a.actor,
                "model_version": a.model_version,
                "policy_version": a.policy_version,
                "playbook_version": a.playbook_version,
                "approver": a.approver,
                "params_digest": a.params_digest,
                "evidence_refs": a.evidence_refs,
                "result": a.result,
                "created_at": a.created_at,
            }
            for a in rows
        ]
    }


@router.get("/api/v1/reports/daily", dependencies=[Depends(require_perm("reports:read"))])
def daily_report(report_date: date | None = Query(default=None, alias="date"), db: Session = Depends(get_db)):
    day = report_date or datetime.now(BEIJING_TZ).date()

    def day_of(dt: datetime | None):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BEIJING_TZ).date()

    tickets = [t for t in db.scalars(select(Ticket)).all() if day_of(t.created_at) == day]
    by_status: dict[str, int] = {}
    for t in tickets:
        by_status[t.status] = by_status.get(t.status, 0) + 1
    recovered = [t for t in tickets if t.status == TicketStatus.recovered.value]
    escalated = [t for t in tickets if t.status == TicketStatus.escalated.value]
    open_items = [
        t
        for t in tickets
        if t.status
        not in {TicketStatus.recovered.value, TicketStatus.escalated.value, TicketStatus.skipped.value}
    ]
    employee = db.get(DigitalEmployee, get_settings().employee_id)
    from app.services.backups import backup_report

    backups = backup_report(db)
    return {
        "date": day.isoformat(),
        "employee_id": get_settings().employee_id,
        "employee_name": employee.name if employee else "",
        "systems": employee.systems if employee else ["订单系统"],
        "planned": len(tickets),
        "completed": len(recovered),
        "by_status": by_status,
        "anomalies": [
            {
                "number": t.number,
                "asset_id": t.asset_id,
                "title": t.title,
                "status": t.status,
                "root_cause": (t.diagnosis or {}).get("root_cause"),
                "policy_light": t.policy_light,
            }
            for t in tickets
        ],
        "backups": backups,
        "open_items": [
            {
                "number": t.number,
                "id": t.id,
                "owner": t.owner,
                "status": t.status,
                "escalate_reason": t.escalate_reason,
                "evidence": True if t.evidence else False,
            }
            for t in open_items + escalated
            if t.status != TicketStatus.recovered.value
        ],
        "note": "「已通知人工」不等于「业务已恢复」。失败不得写成功。",
    }
