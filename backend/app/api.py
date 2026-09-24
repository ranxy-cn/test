from __future__ import annotations

import csv
import hmac
import io
import threading
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.domain.catalog import dump_catalog
from app.models import (
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
from app.services.alert_policy import DEFAULT_POLICY, apply_policy, normalize_policy, read_applied_policy
from app.services.audit import add_audit, add_event
from app.services.notify import notify_ticket
from app.services.pipeline import approve_ticket, dispatch_investigation, in_maintenance, reject_ticket
from app.services.tickets import make_idempotency_key, next_ticket_number
from app.integrations.zabbix.mapping import find_asset_by_zabbix
from pydantic import BaseModel, Field

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
        "zabbix_host": a.zabbix_host,
        "ip": prov.get("ip", ""),
        "provision_status": prov.get("status", ""),
        "provision_logs": "\n".join(prov.get("logs", [])[-8:]),
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
    """资产台账：分页 + 关键字（id/主机名/Zabbix host/应用/负责人）+ 环境/角色过滤。"""
    q = db.query(Asset)
    if keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.filter(
            or_(
                Asset.id.like(kw),
                Asset.hostname.like(kw),
                Asset.zabbix_host.like(kw),
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


ASSET_CSV_COLUMNS = ["id", "hostname", "zabbix_host", "external_id", "app", "role", "env", "owner"]


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


def _zabbix_cfg(mother: Asset | None) -> dict:
    """母机上登记的 Zabbix 实例连接信息（extra["zabbix"]）。空 dict 表示回落全局配置。"""
    z = ((mother.extra or {}).get("zabbix") or {}) if mother else {}
    return z if z.get("url") else {}


def zabbix_client_for(db: Session, asset: Asset):
    """按资产所属母机实例化 Zabbix 客户端：优先母机登记的实例，否则回落全局配置。"""
    from app.integrations.zabbix.http import build_http_zabbix

    mother_id = asset.mother_id or (asset.id if asset.kind == "mother" else "") or get_settings().mother_asset_id
    mother = db.get(Asset, mother_id)
    cfg = _zabbix_cfg(mother)
    if cfg:
        s = get_settings()
        from app.integrations.zabbix.http import HttpZabbixClient

        return HttpZabbixClient(
            cfg["url"],
            cfg.get("token", ""),
            username=cfg.get("user", ""),
            password=cfg.get("password", ""),
            timeout=s.zabbix_timeout_seconds,
            retries=s.zabbix_retries,
            verify_ssl=cfg.get("verify_ssl", True),
        )
    return build_http_zabbix()


def _children_metrics_summary(client: Any, rows: list[dict]) -> dict[str, dict[str, float | None]]:
    """批量取子机 4 项指标最新值（item.get 自带 lastvalue，N 台子机仅 5 次 RPC）。

    返回 {asset_id: {cpu, mem, disk, load}}；任何失败都静默降级（前端显示 "-"）。
    """
    out: dict[str, dict[str, float | None]] = {}
    try:
        hosts = client._rpc("host.get", {"output": ["hostid", "host", "name"]}) or []
        by_name: dict[str, str] = {}
        for h in hosts:
            by_name[str(h.get("host"))] = str(h["hostid"])
            by_name[str(h.get("name"))] = str(h["hostid"])
        hid_to_assets: dict[str, list[str]] = {}
        for r in rows:
            hid = by_name.get(str(r.get("zabbix_host") or "")) or by_name.get(str(r.get("hostname") or ""))
            if hid:
                hid_to_assets.setdefault(hid, []).append(r["id"])
        if not hid_to_assets:
            return out
        hostids = list(hid_to_assets)

        def fetch(key_frag: str) -> dict[str, list[dict]]:
            items = client._rpc(
                "item.get",
                {
                    "output": ["itemid", "hostid", "key_", "lastvalue"],
                    "hostids": hostids,
                    "monitored": True,
                    "search": {"key_": key_frag},
                    "limit": 1000,
                },
            ) or []
            grouped: dict[str, list[dict]] = {}
            for it in items:
                grouped.setdefault(str(it.get("hostid")), []).append(it)
            return grouped

        def val_of(it: dict) -> float | None:
            try:
                v = float(it.get("lastvalue"))
            except (TypeError, ValueError):
                return None
            return round(v, 2) if -1e12 < v < 1e12 else None

        def pick(items: list[dict], prefers: list[str]) -> float | None:
            for p in prefers:
                for it in items:
                    if p in str(it.get("key_", "")) and val_of(it) is not None:
                        return val_of(it)
            for it in items:
                if val_of(it) is not None:
                    return val_of(it)
            return None

        cpu_g, mem_g, disk_g, load_g = (
            fetch("system.cpu.util"),
            fetch("vm.memory"),
            fetch("vfs.fs.size"),
            fetch("system.cpu.load"),
        )

        def mem_used(items: list[dict]) -> float | None:
            # 与监控详情口径一致：统一为"已用%"。utilization 直接是已用；
            # pavailable 是剩余%，需 100 - x（旧逻辑直接取 pavailable，卡片误显示成"可用%"）
            for it in items:
                if "utilization" in str(it.get("key_", "")) and val_of(it) is not None:
                    return val_of(it)
            for it in items:
                if "pavailable" in str(it.get("key_", "")) and val_of(it) is not None:
                    return round(100.0 - val_of(it), 2)
            return None

        def load1(items: list[dict]) -> float | None:
            # 与监控详情口径一致：1 分钟平均负载（system.cpu.load[all,avg1]），
            # 旧逻辑取任意 load 项（可能是 avg5/avg15），与详情 load1 对不上
            return pick(items, ["avg1"])

        def disk_used(items: list[dict]) -> float | None:
            # 与监控详情口径一致：优先根分区已用%（[/,pused]），否则任意 pused 挂载点；
            # 不再回退到 vfs.fs.size 的 total/used 等字节值（会被误当百分比）
            for pref in ("[/,pused]", "pused"):
                for it in items:
                    if pref in str(it.get("key_", "")) and val_of(it) is not None:
                        return val_of(it)
            return None

        for hid, aids in hid_to_assets.items():
            snap = {
                "cpu": pick(cpu_g.get(hid, []), ["system.cpu.util"]),
                "mem": mem_used(mem_g.get(hid, [])),
                "disk": disk_used(disk_g.get(hid, [])),
                "load": load1(load_g.get(hid, [])),
            }
            for aid in aids:
                out[aid] = snap
        return out
    except Exception:  # noqa: BLE001
        return out


def _overview_payload(db: Session, mother: Asset | None) -> dict:
    """母机总览：母机本体 + 其子机按业务分组的统计。

    子机口径：mother_id 指向该母机的子机 + 母机自身（Zabbix 栈自带的 agent 容器
    监控，行上 is_mother=True，不可删除/编辑）。归属规则：存量未指定母机的子机
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
    # 母机自身：部署时自带 agent 容器（Zabbix "Zabbix server" 主机），默认显示为一台子机。
    # 仅当母机已绑定 Zabbix 实例（绑定模式登记或部署成功写回 url）才显示；
    # 未部署/部署失败的母机没有该主机，显示出来就是一台删不掉的 127.0.0.1 假子机
    if mother is not None and bool(((mother.extra or {}).get("zabbix") or {}).get("url")):
        prov = (mother.extra or {}).get("provision") or {}
        mrow = {
            "id": f"{mother.id}:self",
            "hostname": mother.hostname or mother.id,
            "app": prov.get("app") or "监控平台",
            "role": "other",
            "env": "prod",
            "owner": mother.owner,
            "group": mother.group or "",
            "kind": "mother_self",
            "mother_id": mother.id,
            "db_mode": "",
            "tenant_id": mother.tenant_id,
            "reachable": bool(mother.reachable),
            "db_ok": None,
            "external_id": "",
            "zabbix_host": "Zabbix server",
            "zabbix_hostid": "",
            "ip": prov.get("ip", ""),
            "provision_status": "",
            "provision_logs": "",
            "last_seen_at": mother.last_seen_at,
            "last_check_at": mother.last_check_at,
            "unreachable_reason": "",
            "metrics": None,
            "is_mother": True,
        }
        # 补 Zabbix 侧主机 ID（监控详情跳转用）与可达状态
        try:
            hosts = zabbix_client_for(db, mother)._rpc(
                "host.get", {"filter": {"host": ["Zabbix server"]}, "selectInterfaces": ["ip", "available"]}
            )
            if hosts:
                mrow["zabbix_hostid"] = hosts[0]["hostid"]
                ifc = (hosts[0].get("interfaces") or [{}])[0]
                if ifc.get("available"):
                    mrow["reachable"] = str(ifc["available"]) != "2"
                # IP 优先显示登记的母机地址；Zabbix 自监控主机的 interface 常是 127.0.0.1，仅作兜底
                if ifc.get("ip") and not mrow["ip"]:
                    mrow["ip"] = ifc["ip"]
        except Exception:  # noqa: BLE001
            pass
        rows.insert(0, mrow)
    # 子机实时指标摘要（CPU/内存/磁盘/负载最新值）；Zabbix 不可达时为 None，前端显示 "-"
    if rows and mother is not None:
        try:
            summary = _children_metrics_summary(zabbix_client_for(db, mother), rows)
            for r in rows:
                if r["id"] in summary:
                    r["metrics"] = summary[r["id"]]
        except Exception:  # noqa: BLE001
            pass
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
    # 未恢复告警数（子机卡片红色徽标）：母机自身行 id 带 ":self" 后缀，按真实资产 id 统计
    real_ids = [r["id"][: -len(":self")] if r["id"].endswith(":self") else r["id"] for r in rows]
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
        real_id = r["id"][: -len(":self")] if r["id"].endswith(":self") else r["id"]
        r["abnormal_count"] = abnormal.get(real_id, 0)
    return {
        "mother": _asset_row(mother) if mother else None,
        "children": rows,
        "groups": [{"name": k, **v} for k, v in sorted(groups.items(), key=lambda kv: (kv[0] == "", kv[0]))],
    }


@router.get("/api/v1/assets/mothers", dependencies=[Depends(require_perm("assets:read"))])
def list_mothers(db: Session = Depends(get_db)):
    """母机列表（kind=mother），附各母机子机数量。注意路由顺序：须在 /assets/{asset_id} 之前。"""
    settings = get_settings()
    items = []
    for a in db.scalars(select(Asset).where(Asset.kind == "mother").order_by(Asset.id)).all():
        row = _asset_row(a)
        is_default = a.id == settings.mother_asset_id
        row["is_default"] = is_default
        # 未归属母机的存量子机只计入默认母机
        q = db.query(Asset).filter(Asset.id != a.id, Asset.kind != "mother")
        q = q.filter(or_(Asset.mother_id == a.id, Asset.mother_id == "")) if is_default else q.filter(Asset.mother_id == a.id)
        # 子机计数含母机自身（overview 会显示一台"本机·母机"子机）；
        # 但未绑定 Zabbix（未部署/部署失败）的母机没有该合成子机，不能 +1，否则母机永远删不掉
        self_ready = bool(((a.extra or {}).get("zabbix") or {}).get("url"))
        row["children_count"] = q.count() + (1 if self_ready else 0)
        z = _zabbix_cfg(a)
        row["zabbix_url"] = z.get("url", "")
        row["zabbix_mode"] = "bound" if z else "global"
        zraw = (a.extra or {}).get("zabbix") or {}
        row["zabbix_web_port"] = int(zraw.get("web_port") or 8081)
        row["zabbix_trapper_port"] = int(zraw.get("trapper_port") or 10051)
        row["deploy"] = _deploy_brief(a)
        items.append(row)
    return {"items": items, "default_id": settings.mother_asset_id}


@router.get("/api/v1/assets/mother", dependencies=[Depends(require_perm("assets:read"))])
def mother_overview(db: Session = Depends(get_db)):
    """默认母机总览（兼容旧前端入口）。"""
    mother = db.get(Asset, get_settings().mother_asset_id)
    return _overview_payload(db, mother)


class MotherCreateIn(BaseModel):
    """新增母机（一期登记 + 绑定 Zabbix 实例；自动安装 Zabbix 栈为二期能力）。

    db_mode：bundled=独立部署 MySQL 容器（默认，官方镜像，干净隔离）
             external=复用目标机已有 MySQL（需 MySQL 5.7~8.0、独立 zabbix 库与账号）。
    ack_risk：必须显式确认知晓部署影响（装 Docker、占端口、持续磁盘写入等）。
    """

    hostname: str = Field(min_length=1, max_length=128)
    ip: str = Field(min_length=3, max_length=64)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    env: str = Field(default="prod", max_length=32)
    owner: str = Field(default="", max_length=64)
    db_mode: str = Field(default="bundled", pattern="^(bundled|external)$")
    external_db: dict | None = None
    zabbix_web_port: int = Field(default=8081, ge=1024, le=65535)
    zabbix_trapper_port: int = Field(default=10051, ge=1024, le=65535)
    zabbix_url: str = Field(default="", max_length=256)
    zabbix_user: str = Field(default="", max_length=64)
    zabbix_password: str = Field(default="", max_length=128)
    deploy_now: bool = False
    ssh_username: str = Field(default="root", max_length=64)
    ssh_password: str = Field(default="", max_length=128)
    ack_risk: bool
    alert_policy: dict | None = None  # 告警策略（阈值/窗口），缺省字段用平台默认值


@router.get("/api/v1/assets/mothers/{mother_id}/overview", dependencies=[Depends(require_perm("assets:read"))])
def mother_overview_by_id(mother_id: str, db: Session = Depends(get_db)):
    """指定母机的总览（母机切换器数据源）。"""
    mother = db.get(Asset, mother_id)
    if mother is None or mother.kind != "mother":
        raise HTTPException(404, "母机不存在")
    return _overview_payload(db, mother)


@router.post("/api/v1/assets/mothers", dependencies=[Depends(require_perm("assets:write"))])
def create_mother(
    body: MotherCreateIn,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """登记一台母机（Zabbix Server 部署地）。

    一期仅登记与 Zabbix 实例绑定（支持绑定目标机已有 Zabbix）；
    一键安装 Zabbix 栈（server+db+web+agent）由二期"部署母机"预案提供。
    """
    if not body.ack_risk:
        raise HTTPException(400, "请先确认部署风险与注意事项（勾选“已知晓”）")
    if body.db_mode == "external":
        ext = body.external_db or {}
        if not (ext.get("host") and ext.get("database") and ext.get("user")):
            raise HTTPException(400, "复用已有 MySQL 需提供 host / database / user")
    if body.deploy_now and not body.ssh_password:
        raise HTTPException(400, "自动部署需要 SSH 密码（仅本次使用，不落库）")
    if body.zabbix_web_port == body.zabbix_trapper_port:
        raise HTTPException(400, "Web 端口与 Agent 上报端口不能相同")
    try:
        alert_policy = normalize_policy(body.alert_policy)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    reserved = {8000, 8080, 3306}
    for p, name in ((body.zabbix_web_port, "Web 端口"), (body.zabbix_trapper_port, "Agent 上报端口")):
        if p in reserved:
            raise HTTPException(400, f"{name} {p} 是平台保留端口，请换一个")

    aid = f"mother-{body.ip.replace('.', '-')}"
    exists = db.get(Asset, aid)
    if exists is not None:
        raise HTTPException(409, f"该 IP 已登记为母机 {aid}")
    if db.query(Asset).filter(Asset.hostname == body.hostname).first():
        raise HTTPException(409, f"主机名已被资产占用：{body.hostname}")

    zcfg: dict = {"web_port": body.zabbix_web_port, "trapper_port": body.zabbix_trapper_port}
    if body.zabbix_url:
        zcfg.update({
            "url": body.zabbix_url.strip(),
            "user": body.zabbix_user,
            "password": body.zabbix_password,
        })
    if body.db_mode == "external":
        ext = body.external_db or {}
        zcfg["db"] = {
            "mode": "external",
            "host": ext.get("host", ""),
            "port": int(ext.get("port") or 3306),
            "user": ext.get("user", ""),
            "password": ext.get("password", ""),
            "database": ext.get("database", "zabbix"),
        }
    else:
        zcfg["db"] = {"mode": "bundled"}

    asset = Asset(
        id=aid,
        hostname=body.hostname.strip(),
        app="Zabbix Server",
        role="mother",
        env=body.env,
        owner=body.owner,
        kind="mother",
        db_mode=body.db_mode,
        tenant_id=get_settings().tenant_id,
        reachable=False,
        extra={
            "zabbix": zcfg,
            "alert_policy": alert_policy,
            # 记录母机 IP：新增节点时 Agent 的 Server= 地址默认取这里（hostname 可能不是 IP）
            "provision": {"ip": body.ip, "port": body.ssh_port, "status": "registered"},
        },
    )
    db.add(asset)
    if body.deploy_now:
        asset.extra = {
            **(asset.extra or {}),
            "deploy": {
                "status": "running",
                "ip": body.ip,
                "port": body.ssh_port,
                "username": body.ssh_username,
                "requested_by": current.username,
                "started_at": utcnow().isoformat(),
                "finished_at": "",
                "error": "",
                "logs": [],
            },
        }
    db.commit()
    add_audit(
        db,
        ticket_id=None,
        event_type="mother_create",
        actor=current.username,
        result={"asset_id": aid, "ip": body.ip, "db_mode": body.db_mode, "deploy_now": body.deploy_now},
    )
    db.commit()
    if body.deploy_now:
        import threading

        from app.services.mother_deploy import deploy_mother

        threading.Thread(
            target=deploy_mother,
            kwargs=dict(
                asset_id=aid,
                ip=body.ip,
                port=body.ssh_port,
                username=body.ssh_username,
                password=body.ssh_password,
                requested_by=current.username,
                web_port=body.zabbix_web_port,
                trapper_port=body.zabbix_trapper_port,
            ),
            daemon=True,
        ).start()
    row = _asset_row(asset)
    row["zabbix_url"] = zcfg.get("url", "")
    row["zabbix_mode"] = "bound" if zcfg.get("url") else "global"
    return row


class AlertPolicyIn(BaseModel):
    """母机告警策略：阈值（%）与触发窗口（分钟），作用于母机 Zabbix 的监控模板。"""

    cpu_threshold: int = Field(ge=1, le=99)
    cpu_window_minutes: int = Field(ge=1, le=120)
    mem_threshold: int = Field(ge=1, le=99)
    mem_window_minutes: int = Field(ge=1, le=120)
    load_threshold: float = Field(ge=0.1, le=100)
    load_window_minutes: int = Field(ge=1, le=120)


def _mother_zabbix_write_client(mother: Asset):
    """母机绑定的 Zabbix 写客户端（模板宏/触发器更新需要非只读）。"""
    from app.config import get_settings as _gs
    from app.integrations.zabbix.http import HttpZabbixClient

    cfg = _zabbix_cfg(mother)
    s = _gs()
    return HttpZabbixClient(
        cfg["url"],
        cfg.get("token", ""),
        username=cfg.get("user") or "Admin",
        password=cfg.get("password") or "zabbix",
        timeout=s.zabbix_timeout_seconds,
        retries=s.zabbix_retries,
        verify_ssl=s.zabbix_verify_ssl,
        readonly=False,
    )


@router.get("/api/v1/assets/mothers/{mother_id}/alert-policy", dependencies=[Depends(require_perm("assets:read"))])
def get_alert_policy(mother_id: str, db: Session = Depends(get_db)):
    """母机告警策略：平台存储值 + 默认值；若已绑定 Zabbix 再附带模板当前生效值。"""
    mother = db.get(Asset, mother_id)
    if mother is None or mother.kind != "mother":
        raise HTTPException(404, "母机不存在")
    resp: dict = {
        "policy": normalize_policy((mother.extra or {}).get("alert_policy")),
        "defaults": dict(DEFAULT_POLICY),
        "has_zabbix": False,
        "applied": None,
        "apply_error": "",
    }
    if _zabbix_cfg(mother):
        resp["has_zabbix"] = True
        try:
            zc = _mother_zabbix_write_client(mother)
            resp["applied"] = read_applied_policy(zc)
        except Exception as exc:  # noqa: BLE001
            resp["apply_error"] = str(exc)
    return resp


@router.put("/api/v1/assets/mothers/{mother_id}/alert-policy", dependencies=[Depends(require_perm("assets:write"))])
def update_alert_policy(
    mother_id: str,
    body: AlertPolicyIn,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """保存母机告警策略；若该母机已绑定 Zabbix，则同步应用到监控模板（母机与子机统一生效）。"""
    mother = db.get(Asset, mother_id)
    if mother is None or mother.kind != "mother":
        raise HTTPException(404, "母机不存在")
    policy = normalize_policy(body.model_dump())
    mother.extra = {**(mother.extra or {}), "alert_policy": policy}

    applied = None
    apply_error = ""
    if _zabbix_cfg(mother):
        try:
            zc = _mother_zabbix_write_client(mother)
            applied = apply_policy(zc, policy)
        except Exception as exc:  # noqa: BLE001
            apply_error = str(exc)
    db.commit()
    add_audit(
        db,
        ticket_id=None,
        event_type="alert_policy_update",
        actor=current.username,
        result={"mother_id": mother_id, "policy": policy, "applied": applied, "error": apply_error},
    )
    db.commit()
    return {"policy": policy, "applied": applied, "apply_error": apply_error}


class MotherDeployIn(BaseModel):
    """在母机上自动安装 Zabbix 栈。SSH 密码仅本次使用，不落库、不写日志。

    zabbix_web_port/zabbix_trapper_port：本次部署实例的端口，传 0 表示沿用母机已登记端口；
    换端口可在同一母机上并行部署多个实例（独立目录/compose 项目名/数据卷）。
    """

    ip: str = Field(min_length=3, max_length=64)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default="root", max_length=64)
    password: str = Field(min_length=1, max_length=128)
    zabbix_web_port: int = Field(default=0, ge=0, le=65535)
    zabbix_trapper_port: int = Field(default=0, ge=0, le=65535)


def _deploy_brief(a: Asset) -> dict:
    d = (a.extra or {}).get("deploy") or {}
    keys = ("status", "ip", "started_at", "finished_at", "error", "version", "web_url", "logs")
    return {k: d.get(k, [] if k == "logs" else "") for k in keys}


@router.get("/api/v1/assets/mothers/{mother_id}/deploy", dependencies=[Depends(require_perm("assets:read"))])
def mother_deploy_status(mother_id: str, db: Session = Depends(get_db)):
    """部署状态查询（前端轮询）。"""
    a = db.get(Asset, mother_id)
    if a is None or a.kind != "mother":
        raise HTTPException(404, "母机不存在")
    return _deploy_brief(a)


@router.post("/api/v1/assets/mothers/{mother_id}/deploy", dependencies=[Depends(require_perm("assets:write"))])
def deploy_mother_stack(
    mother_id: str,
    body: MotherDeployIn,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """一键在母机上安装 Zabbix 栈（server+db+web+agent），后台执行。"""
    import threading

    from app.services.mother_deploy import deploy_mother

    a = db.get(Asset, mother_id)
    if a is None or a.kind != "mother":
        raise HTTPException(404, "母机不存在")
    if ((a.extra or {}).get("deploy") or {}).get("status") == "running":
        raise HTTPException(409, "该母机已有部署任务在执行中")
    # 端口校验：显式指定（非 0）时互斥且避开平台保留端口；0 由 deploy_mother 回退母机已登记端口
    zcfg = dict((a.extra or {}).get("zabbix") or {})
    web_port = body.zabbix_web_port or int(zcfg.get("web_port") or 8081)
    trapper_port = body.zabbix_trapper_port or int(zcfg.get("trapper_port") or 10051)
    if web_port == trapper_port:
        raise HTTPException(400, "Web 端口与 Agent 上报端口不能相同")
    reserved = {8000, 8080, 3306}
    for p, name in ((web_port, "Web 端口"), (trapper_port, "Agent 上报端口")):
        if p in reserved:
            raise HTTPException(400, f"{name} {p} 是平台保留端口，请换一个")
    a.extra = {
        **(a.extra or {}),
        "deploy": {
            "status": "running",
            "ip": body.ip,
            "port": body.port,
            "username": body.username,
            "requested_by": current.username,
            "started_at": utcnow().isoformat(),
            "finished_at": "",
            "error": "",
            "logs": [],
        },
    }
    db.add(a)
    db.commit()
    threading.Thread(
        target=deploy_mother,
        kwargs=dict(
            asset_id=mother_id,
            ip=body.ip,
            port=body.port,
            username=body.username,
            password=body.password,
            requested_by=current.username,
            web_port=body.zabbix_web_port,
            trapper_port=body.zabbix_trapper_port,
        ),
        daemon=True,
    ).start()
    return {"status": "running", "asset_id": mother_id}


class MotherUninstallIn(BaseModel):
    """删除母机并（可选）远程卸载 Zabbix 栈。SSH 密码仅本次使用，不落库、不写日志。"""

    ip: str = Field(min_length=3, max_length=64)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default="root", max_length=64)
    password: str = Field(min_length=1, max_length=128)


@router.post("/api/v1/assets/mothers/{mother_id}/uninstall", dependencies=[Depends(require_perm("assets:write"))])
def uninstall_mother(
    mother_id: str,
    body: MotherUninstallIn,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """删除母机：先 SSH 到目标机卸载 Zabbix 栈（停止容器+删除安装目录），成功后移除台账记录。

    任一步失败均不删除记录，避免"台账删了但服务器上还装着"的脏状态。
    """
    import threading as _threading

    from app.services.mother_deploy import ProvisionError, uninstall_stack

    a = db.get(Asset, mother_id)
    if a is None or a.kind != "mother":
        raise HTTPException(404, "母机不存在")
    # 未部署成功（未绑定 Zabbix 实例）的母机，服务器上没有 Zabbix 栈可卸载：
    # SSH 卸载只会白白失败（还常因凭据问题报 Authentication failed），直接引导走仅删记录
    if not (((a.extra or {}).get("zabbix") or {}).get("url") or "").strip():
        raise HTTPException(
            400,
            "该母机未部署成功，服务器上没有 Zabbix 栈可卸载；请取消勾选「卸载」，直接删除记录即可",
        )
    n_children = db.query(Asset).filter(Asset.kind != "mother", Asset.mother_id == mother_id).count()
    if n_children:
        raise HTTPException(409, f"该母机名下还有 {n_children} 台子机，请先删除或迁移子机")
    refs = {
        "tickets": db.query(Ticket).filter(Ticket.asset_id == mother_id).count(),
        "maintenance_windows": db.query(MaintenanceWindow).filter(MaintenanceWindow.asset_id == mother_id).count(),
        "backup_jobs": db.query(BackupJob).filter(BackupJob.asset_id == mother_id).count(),
    }
    if any(refs.values()):
        raise HTTPException(409, f"资产被引用，先处理关联数据: {refs}")

    # 卸载是长耗时 SSH 操作（compose down 最长 3 分钟），放后台线程执行，前端轮询进度
    a.extra = {
        **(a.extra or {}),
        "deploy": {
            "status": "uninstalling",
            "ip": body.ip,
            "port": body.port,
            "username": body.username,
            "requested_by": current.username,
            "started_at": utcnow().isoformat(),
            "finished_at": "",
            "error": "",
            "logs": [],
        },
    }
    db.add(a)
    db.commit()

    def _do_uninstall() -> None:
        from app.database import SessionLocal

        logs: list[str] = []
        ok = False
        err = ""
        try:
            uninstall_stack(body.ip, body.port, body.username, body.password, logs)
            ok = True
        except Exception as exc:  # noqa: BLE001
            err = str(exc)[:300]
            logs.append(f"卸载失败：{err}")
        db2 = SessionLocal()
        try:
            a2 = db2.get(Asset, mother_id)
            if a2 is None:
                return
            if ok:
                db2.delete(a2)
            else:
                # 卸载失败保留记录，状态回 failed 供用户重试或改为仅删记录
                a2.extra = {
                    **(a2.extra or {}),
                    "deploy": {
                        "status": "failed",
                        "ip": body.ip,
                        "port": body.port,
                        "username": body.username,
                        "requested_by": current.username,
                        "started_at": utcnow().isoformat(),
                        "finished_at": utcnow().isoformat(),
                        "error": f"卸载失败（记录未删除）：{err}",
                        "logs": logs[-300:],
                    },
                }
                db2.add(a2)
            add_audit(
                db2,
                ticket_id=None,
                event_type="mother_uninstall",
                actor=current.username,
                result={"asset_id": mother_id, "ip": body.ip, "status": "deleted" if ok else "failed", "error": err},
            )
            db2.commit()
        finally:
            db2.close()

    threading.Thread(target=_do_uninstall, daemon=True).start()
    return {"status": "uninstalling", "asset_id": mother_id}


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


@router.get("/api/v1/assets/mothers/{mother_id}/deploy-detail", dependencies=[Depends(require_perm("assets:read"))])
def mother_deploy_detail(mother_id: str, db: Session = Depends(get_db)):
    """母机安装详情：安装位置、端口、容器清单、Zabbix 控制台账号密码等完整信息。"""
    from app.services.mother_deploy import DEFAULT_TRAPPER_PORT, DEFAULT_WEB_PORT, stack_name_for

    a = db.get(Asset, mother_id)
    if a is None or a.kind != "mother":
        raise HTTPException(404, "母机不存在")
    extra = dict(a.extra or {})
    z = dict(extra.get("zabbix") or {})
    d = dict(extra.get("deploy") or {})
    dbcfg = dict(z.get("db") or {})
    # 资产 IP 不在列上：纳管登记 > 部署登记
    asset_ip = str(d.get("ip") or (extra.get("provision") or {}).get("ip") or "")
    web_port = int(z.get("web_port") or DEFAULT_WEB_PORT)
    trapper_port = int(z.get("trapper_port") or DEFAULT_TRAPPER_PORT)
    stack_dir = stack_name_for(web_port)
    web_url = str(z.get("url") or d.get("web_url") or (f"http://{asset_ip}:{web_port}" if asset_ip else ""))
    db_mode = dbcfg.get("mode") or a.db_mode or "bundled"
    bundled = db_mode == "bundled"
    return {
        "id": a.id,
        "hostname": a.hostname,
        "ip": asset_ip,
        "env": a.env,
        "owner": a.owner,
        "group": a.group,
        "reachable": a.reachable,
        "deploy": {
            "status": d.get("status", ""),
            "version": d.get("version", ""),
            "started_at": d.get("started_at", ""),
            "finished_at": d.get("finished_at", ""),
            "error": d.get("error", ""),
        },
        "ssh": {"ip": d.get("ip") or asset_ip, "port": int(d.get("port") or 22), "username": d.get("username", "")},
        "install": {
            "method": "Docker Compose（Zabbix 官方 5.0 LTS alpine 镜像）",
            "dir": f"~/{stack_dir}",
            "compose_file": f"~/{stack_dir}/docker-compose.yml",
            "env_file": f"~/{stack_dir}/.env",
            "data_dir": f"~/{stack_dir}/mysql-data" if bundled else "—（使用外部数据库）",
            "containers": (
                ["mysql", "zabbix-server", "zabbix-web", "zabbix-agent"] if bundled else ["zabbix-server", "zabbix-web", "zabbix-agent"]
            ),
        },
        "ports": {"web": web_port, "trapper": trapper_port},
        "zabbix": {
            "web_url": web_url,
            "api_url": f"{web_url}/api_jsonrpc.php",
            "user": z.get("user", ""),
            "password": z.get("password", ""),
        },
        "db": {
            "mode": db_mode,
            "host": dbcfg.get("host") or ("mysql（本机容器）" if bundled else ""),
            "port": int(dbcfg.get("port") or 3306),
            "database": dbcfg.get("database") or "zabbix",
            "user": dbcfg.get("user") or ("zabbix" if bundled else ""),
        },
        "logs": d.get("logs") or [],
    }


class VerifyZabbixIn(BaseModel):
    """校验 Zabbix API 连通性（新增母机表单"测试连接"）。凭据仅本次使用，不落库。"""

    url: str = Field(min_length=3, max_length=256)
    user: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=128)


@router.post("/api/v1/assets/verify-zabbix", dependencies=[Depends(require_perm("assets:write"))])
def verify_zabbix(body: VerifyZabbixIn):
    from app.integrations.zabbix.http import HttpZabbixClient

    client = HttpZabbixClient(body.url, username=body.user, password=body.password, retries=1)
    try:
        health = client.health()
        return {"ok": bool(health.get("ok")), "version": health.get("version"), "error": health.get("last_error") or ""}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "version": None, "error": str(exc)}


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
    zcfg = dict(extra.get("zabbix") or {})
    if zcfg.get("password"):
        zcfg["password"] = "******"
    if isinstance(zcfg.get("db"), dict) and zcfg["db"].get("password"):
        zcfg["db"] = {**zcfg["db"], "password": "******"}
    if zcfg:
        extra["zabbix"] = zcfg
    row["extra"] = extra
    row["tickets"] = db.query(Ticket).filter(Ticket.asset_id == asset_id).count()
    row["maintenance_windows"] = db.query(MaintenanceWindow).filter(MaintenanceWindow.asset_id == asset_id).count()
    row["backup_jobs"] = db.query(BackupJob).filter(BackupJob.asset_id == asset_id).count()
    return row


@router.delete("/api/v1/assets/{asset_id}", dependencies=[Depends(require_perm("assets:write"))])
def delete_asset(asset_id: str, db: Session = Depends(get_db), current: CurrentUser = Depends(require_perm("assets:write"))):
    """删除资产；被工单/维护窗口/备份任务引用时拒绝。"""
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    refs = {
        "tickets": db.query(Ticket).filter(Ticket.asset_id == asset_id).count(),
        "maintenance_windows": db.query(MaintenanceWindow).filter(MaintenanceWindow.asset_id == asset_id).count(),
        "backup_jobs": db.query(BackupJob).filter(BackupJob.asset_id == asset_id).count(),
    }
    if any(refs.values()):
        raise HTTPException(409, f"资产被引用，先处理关联数据: {refs}")
    if a.kind == "mother":
        # 母机名下还有子机时不允许删除，避免子机悬空
        n_children = db.query(Asset).filter(Asset.kind != "mother", Asset.mother_id == asset_id).count()
        if n_children:
            raise HTTPException(409, f"该母机名下还有 {n_children} 台子机，请先删除或迁移子机")
    db.delete(a)
    add_audit(
        db,
        ticket_id=None,
        event_type="asset_delete",
        actor=current.username,
        result={"asset_id": asset_id, "hostname": a.hostname},
    )
    db.commit()
    return {"deleted": asset_id}


@router.post("/api/v1/assets/probe", dependencies=[Depends(require_perm("assets:read"))])
def probe_assets_now(db: Session = Depends(get_db)):
    """立即对全部资产做一轮连通性探测（TCP 探活），返回本轮统计。"""
    from app.services.probe import run_probe_cycle

    return run_probe_cycle(db)


class AssetRemoveIn(BaseModel):
    """删除子机：可选先 SSH 卸载远端 zabbix-agent（安装密码不落库）。"""

    uninstall: bool = False
    ssh_password: str = Field(default="", max_length=128)


@router.post("/api/v1/assets/{asset_id}/remove")
def remove_asset(
    asset_id: str,
    body: AssetRemoveIn,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    """删除子机资产；勾选卸载时先 SSH 到来源机卸载 zabbix-agent 并从 Zabbix 注销主机。"""
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    # 母机允许"仅删除记录"（卸载 Zabbix 栈走 /mothers/{id}/uninstall 专用接口）；
    # 未部署成功的母机没有可卸载的东西，必须能直接删除，否则永远删不掉
    if a.kind == "mother" and body.uninstall:
        raise HTTPException(400, "母机卸载请走「删除母机」弹窗的「卸载并删除」流程")
    refs = {
        "tickets": db.query(Ticket).filter(Ticket.asset_id == asset_id).count(),
        "maintenance_windows": db.query(MaintenanceWindow).filter(MaintenanceWindow.asset_id == asset_id).count(),
        "backup_jobs": db.query(BackupJob).filter(BackupJob.asset_id == asset_id).count(),
    }
    if any(refs.values()):
        raise HTTPException(409, f"资产被引用，先处理关联数据: {refs}")

    # 1) 远程卸载 zabbix-agent（纳管来源机；密码仅本次使用）
    uninstall_logs: list[str] = []
    prov = (a.extra or {}).get("provision") or {}
    if body.uninstall:
        ip = str(prov.get("ip") or "").strip()
        port = int(prov.get("port") or 22)
        username = str(prov.get("username") or "root")
        if not ip:
            raise HTTPException(400, "该资产没有纳管来源（ip/端口），无法远程卸载；请取消勾选「卸载 agent」后直接删除")
        if not body.ssh_password:
            raise HTTPException(400, "请输入该服务器的 SSH 密码用于卸载 zabbix-agent")
        try:
            ssh = provision_svc._connect_ssh(ip, port, username, body.ssh_password)
            try:
                provision_svc.uninstall_agent_via_ssh(ssh, password=body.ssh_password, logs=uninstall_logs)
            finally:
                ssh.close()
        except provision_svc.ProvisionError as exc:
            raise HTTPException(502, f"远程卸载失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"SSH 连接失败（{ip}:{port}）：{exc}")

    # 2) Zabbix 侧注销监控主机（最佳努力，失败不阻断删除）
    zbx_note = ""
    try:
        client = zabbix_client_for(db, a)
        hostid = a.external_id or ""
        if not hostid and a.zabbix_host:
            hosts = client._rpc("host.get", {"filter": {"host": [a.zabbix_host]}, "output": ["hostid"]}) or []
            hostid = str(hosts[0]["hostid"]) if hosts else ""
        if hostid:
            client._rpc("host.delete", [hostid])
            zbx_note = f"已在 Zabbix 注销主机（hostid={hostid}）"
    except Exception as exc:  # noqa: BLE001
        zbx_note = f"Zabbix 注销失败（不影响删除）：{str(exc)[:200]}"

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
            "zabbix": zbx_note,
        },
    )
    db.commit()
    return {"deleted": asset_id, "uninstalled": body.uninstall, "uninstall_logs": uninstall_logs[-8:], "zabbix": zbx_note}


@router.get("/api/v1/assets/{asset_id}/provision", dependencies=[Depends(require_perm("assets:read"))])
def get_asset_provision(asset_id: str, db: Session = Depends(get_db)):
    """纳管详情：安装状态、完整安装日志与安装信息（Agent 版本/安装位置/运行方式）。"""
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    prov = (a.extra or {}).get("provision") or {}
    return {
        "asset_id": asset_id,
        "status": prov.get("status", ""),
        "ip": prov.get("ip", ""),
        "port": prov.get("port", 22),
        "username": prov.get("username", ""),
        "zabbix_server": prov.get("zabbix_server", ""),
        "started_at": prov.get("started_at", ""),
        "finished_at": prov.get("finished_at", ""),
        "error": prov.get("error", ""),
        "install_info": prov.get("install_info") or None,
        "logs": prov.get("logs", []),
    }


def _mock_asset_metrics(asset_id: str, minutes: int) -> dict:
    """Zabbix 未接入（mock 模式）时的演示序列：最近 N 分钟、每分钟一个点。"""
    import random

    now = int(utcnow().timestamp())
    n = max(10, min(int(minutes), 240))

    def walk(base: float, spread: float, low: float, high: float) -> list:
        vals, cur = [], base
        for i in range(n):
            cur = min(high, max(low, cur + random.uniform(-spread, spread) + (base - cur) * 0.1))
            vals.append({"t": str(now - (n - 1 - i) * 60), "v": round(cur, 1)})
        return vals

    series = {
        "cpu": walk(45.0, 6.0, 3.0, 97.0),
        "mem": walk(68.0, 1.5, 20.0, 95.0),
        "disk": walk(38.0, 0.1, 5.0, 98.0),
        "load": walk(1.2, 0.3, 0.0, 16.0),
    }
    latest = {k: (v[-1]["v"] if v else None) for k, v in series.items()}
    return {"mapped": True, "real": False, "asset_id": asset_id, "minutes": minutes, "latest": latest, "series": series}


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
    """资产监控大盘：CPU/内存/磁盘/负载 折线（已注册机器走 Zabbix，未接入返回演示序列）。"""
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "资产不存在")
    settings = get_settings()
    if settings.integration_mode == "real" and (settings.zabbix_url or _zabbix_cfg(
        db.get(Asset, a.mother_id or (a.id if a.kind == "mother" else "") or settings.mother_asset_id)
    )):
        try:
            client = zabbix_client_for(db, a)
            hint = {"external_id": a.external_id, "zabbix_host": a.zabbix_host, "hostname": a.hostname}
            if a.kind == "mother":
                # 母机自身监控由 Zabbix 栈自带的 agent 容器上报，主机名固定为 "Zabbix server"
                hint["zabbix_host"] = "Zabbix server"
            data = client.asset_metrics(asset_id, hint, minutes)
            return AssetMetricsOut(
                mapped=bool(data.get("mapped")),
                real=True,
                asset_id=asset_id,
                minutes=minutes,
                latest=data.get("latest") or {},
                series=data.get("series") or {},
                note="" if data.get("mapped") else str(data.get("note") or "未在 Zabbix 中匹配到主机"),
            )
        except Exception as exc:  # noqa: BLE001
            mock = _mock_asset_metrics(asset_id, minutes)
            return AssetMetricsOut(mapped=True, real=False, asset_id=asset_id, minutes=minutes,
                                   latest=mock["latest"], series=mock["series"],
                                   note=f"Zabbix 拉取失败，展示演示数据：{exc}")
    mock = _mock_asset_metrics(asset_id, minutes)
    return AssetMetricsOut(mapped=True, real=False, asset_id=asset_id, minutes=minutes,
                           latest=mock["latest"], series=mock["series"],
                           note="Zabbix 未接入（mock 模式），展示演示数据")


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
    """登记/更新资产（供 add-node.sh 自动纳管回写，id 建议用主机名）。"""

    id: str = Field(min_length=1, max_length=64)
    hostname: str = ""
    app: str = ""
    role: str = "app"
    env: str = "prod"
    owner: str = ""
    zabbix_host: str = ""
    zabbix_server: str = ""
    source: str = "add-node"


@router.post("/api/v1/assets/upsert", dependencies=[Depends(require_perm("assets:write"))])
def upsert_asset(body: AssetUpsertIn, db: Session = Depends(get_db)):
    settings = get_settings()
    asset = db.get(Asset, body.id)
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
            zabbix_host=body.zabbix_host,
        )
        db.add(asset)
    else:
        if body.hostname:
            asset.hostname = body.hostname
        if body.owner:
            asset.owner = body.owner
        if body.zabbix_host:
            asset.zabbix_host = body.zabbix_host
        asset.reachable = True
    extra = dict(asset.extra or {})
    extra["zabbix_agent"] = {
        **(extra.get("zabbix_agent") or {}),
        "source": body.source,
        "zabbix_server": body.zabbix_server,
        "synced_at": utcnow().isoformat(),
    }
    asset.extra = extra
    add_audit(
        db,
        ticket_id=None,
        event_type="asset_upsert",
        actor=body.source,
        result={"asset_id": asset.id, "zabbix_host": asset.zabbix_host},
    )
    db.commit()
    return {
        "id": asset.id,
        "hostname": asset.hostname,
        "zabbix_host": asset.zabbix_host,
        "reachable": asset.reachable,
        "created": asset.extra["zabbix_agent"]["synced_at"],
    }


class AssetProvisionIn(BaseModel):
    """资产页一键纳管：SSH 密码直连新机装 Zabbix Agent。密码仅本次安装使用，不落库。"""

    display_name: str = Field(default="", max_length=64, description="子机显示名；空=用安装后的真实主机名")
    ip: str = Field(min_length=3, max_length=64)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default="root", max_length=64)
    password: str = Field(min_length=1, max_length=128)
    zabbix_server: str = Field(default="", max_length=128)
    app: str = Field(default="", max_length=64)
    role: str = Field(default="app", max_length=64)
    env: str = Field(default="prod", max_length=32)
    owner: str = Field(default="", max_length=64)
    group: str = Field(default="", max_length=64)
    mother_id: str = Field(default="", max_length=64, description="归属母机；空=默认母机")


@router.post("/api/v1/assets/provision")
def provision_asset(
    body: AssetProvisionIn,
    current: CurrentUser = Depends(require_perm("assets:write")),
    db: Session = Depends(get_db),
):
    # 归属母机：显式指定 → 校验存在；未指定 → 默认母机（不存在则留空，兼容存量流程）。
    # Zabbix Server 地址默认指向母机 IP。
    requested_mother = body.mother_id.strip()
    mother_id = requested_mother or get_settings().mother_asset_id
    mother = db.get(Asset, mother_id)
    if requested_mother and mother is None:
        raise HTTPException(404, f"归属母机不存在：{mother_id}")
    if mother is not None and mother.kind != "mother":
        raise HTTPException(400, f"资产 {mother_id} 不是母机")
    if mother is not None:
        # 母机自身已随部署附带 agent 容器（Zabbix "Zabbix server" 主机），禁止重复纳管
        mother_ip = str(((mother.extra or {}).get("provision") or {}).get("ip") or mother.hostname or "").strip()
        if mother_ip and body.ip.strip() == mother_ip:
            raise HTTPException(
                400, f"{body.ip} 是母机「{mother.hostname or mother.id}」自身，已自带监控 agent，无需也不可重复纳管"
            )
    zserver = provision_svc.resolve_zabbix_server(body.zabbix_server)
    if mother is not None and not body.zabbix_server.strip():
        zcfg = (mother.extra or {}).get("zabbix") or {}
        mother_ip = str(((mother.extra or {}).get("provision") or {}).get("ip") or mother.hostname or "")
        if mother_ip and "." in mother_ip:
            trap = int(zcfg.get("trapper_port") or 10051)
            # Agent 脚本兼容 ip 与 ip:port 两种形式；非默认端口必须显式带上
            zserver = mother_ip if trap == 10051 else f"{mother_ip}:{trap}"
    if not zserver:
        raise HTTPException(
            400, "Zabbix Server 地址未提供且系统未配置（ZABBIX_URL / PROVISION_ZABBIX_SERVER），请在表单里填写"
        )
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
            mother_id=mother_id,
            tenant_id=get_settings().tenant_id,
            reachable=False,
            extra={"provision": {"status": "running", "ip": body.ip, "port": body.port}},
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
        extra["provision"] = {"status": "running", "ip": body.ip, "port": body.port}
        asset.extra = extra
    db.add(asset)
    db.commit()

    # 后台线程执行装机（SSH 安装约 1 分钟），接口立即返回
    threading.Thread(
        target=provision_svc.provision_node,
        kwargs=dict(
            asset_id=aid,
            display_name=body.display_name.strip(),
            ip=body.ip,
            port=body.port,
            username=body.username,
            password=body.password,
            zabbix_server=zserver,
            requested_by=current.username,
        ),
        daemon=True,
        name=f"provision-{aid}",
    ).start()
    return {
        "id": aid,
        "status": "running",
        "zabbix_server": zserver,
        "message": "开始纳管：正在连接新机安装 Zabbix Agent（约 1~3 分钟），资产列表将自动刷新",
    }


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
    """Zabbix 告警入口：记录「异常/恢复」两态条目（按 event_id 幂等更新）。

    默认不自动立案；WEBHOOK_AUTO_TICKET=true 时兼容旧的自动立案管线。
    """
    recovered = str(body.value or "").upper() in {"OK", "RESOLVED"}
    asset = find_asset_by_zabbix(
        db,
        asset_id=body.asset_id,
        host=body.host,
        hostname=body.hostname,
        hostid=body.hostid,
    )
    # 母机自身监控由 Zabbix 栈自带 agent 容器上报，主机名固定为 "Zabbix server"，
    # 资产台账里没有对应子机记录时，归属到母机自身。
    if asset is None and (body.host or body.hostname or "").strip().lower() == "zabbix server":
        asset = db.get(Asset, get_settings().mother_asset_id) or db.scalar(
            select(Asset).where(Asset.kind == "mother").limit(1)
        )

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

    # 首次出现的异常：后台自动 SSH 采集异常时刻进程快照
    if not recovered and not duplicate:
        threading.Thread(target=diagnostics_svc.collect_for_anomaly, args=(anomaly.id,), daemon=True).start()

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
    add_event(db, ticket_id=ticket.id, kind="alert_received", message=f"Zabbix 告警立案 {ticket.number}")
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
            # 母机自身 agent（"Zabbix server" 主机）上报：母机自身也是一台子机（本机·母机）
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
    threading.Thread(target=diagnostics_svc.collect_for_anomaly, args=(aid,), daemon=True).start()
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
