from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import (
    AnomalyEvent,
    Approval,
    Asset,
    MetricSample,
    SystemMetricSample,
    Ticket,
    TicketEvent,
)
from app.routers.deps import require_perm
from app.services.netdata import collect_snapshot

router = APIRouter()


def _netdata_summary(snapshot: dict) -> dict:
    return {
        "asset_id": snapshot["asset_id"],
        "hostname": snapshot["hostname"],
        "configured": snapshot["configured"],
        "online": snapshot["online"],
        "metrics": snapshot.get("metrics") or {},
        "info": snapshot.get("info") or {},
        "error": snapshot.get("error", ""),
        "checked_at": snapshot.get("checked_at"),
    }


@router.get("/api/v1/dashboard/overview", dependencies=[Depends(require_perm("dashboard:read"))])
def dashboard_overview(db: Session = Depends(get_db)):
    assets = db.scalars(
        select(Asset).where(
            (Asset.kind == "mother") | ((Asset.kind == "child") & (Asset.mother_id != ""))
        ).order_by(Asset.hostname)
    ).all()
    active_count = db.scalar(
        select(func.count()).select_from(AnomalyEvent).where(AnomalyEvent.status == "abnormal")
    ) or 0
    active_anomalies = db.scalars(
        select(AnomalyEvent).where(AnomalyEvent.status == "abnormal").order_by(AnomalyEvent.last_seen_at.desc()).limit(10)
    ).all()
    open_tickets = db.scalar(
        select(func.count()).select_from(Ticket).where(Ticket.status.not_in(["recovered", "skipped"]))
    ) or 0
    recovered_tickets = db.scalar(
        select(func.count()).select_from(Ticket).where(Ticket.status == "recovered")
    ) or 0
    since = datetime.now(timezone.utc) - timedelta(days=7)
    trend_rows = db.execute(
        select(func.date(AnomalyEvent.last_seen_at), func.count())
        .where(AnomalyEvent.last_seen_at >= since)
        .group_by(func.date(AnomalyEvent.last_seen_at))
        .order_by(func.date(AnomalyEvent.last_seen_at))
    ).all()
    # 告警等级分布：直接全量统计（anomalies 列表只取前 10 条，用列表统计会被截断）
    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for severity, count in db.execute(
        select(AnomalyEvent.severity, func.count())
        .where(AnomalyEvent.status == "abnormal")
        .group_by(AnomalyEvent.severity)
    ).all():
        text = str(severity or "").lower()
        if any(word in text for word in ("high", "critical", "严重", "高")):
            severity_counts["high"] += count
        elif any(word in text for word in ("medium", "warning", "warn", "警告", "中")):
            severity_counts["medium"] += count
        else:
            severity_counts["low"] += count

    # 趋势按自然日补齐 7 天（无告警的日期补 0），避免"近 7 日"只显示有数据的 2 天
    # 以数据库当前日期为准，保证与 func.date(...) 的分组口径一致
    counts = {str(day): count for day, count in trend_rows}
    today = db.scalar(select(func.current_date())) or datetime.now(timezone.utc).date()
    trend_7d = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        trend_7d.append({"date": str(day), "count": int(counts.get(str(day), 0))})

    payload = {
        "generated_at": datetime.now(timezone.utc),
        "kpis": {
            "asset_total": len(assets),
            "asset_reachable": sum(1 for asset in assets if asset.reachable),
            "active_anomalies": active_count,
            "open_tickets": open_tickets,
            "recovered_tickets": recovered_tickets,
            "availability": round(sum(1 for asset in assets if asset.reachable) / len(assets) * 100, 1) if assets else 100,
        },
        "trend": trend_7d,
        "severity": severity_counts,
        "assets": [
            {"hostname": asset.hostname, "app": asset.app, "group": asset.group, "reachable": asset.reachable, "db_ok": asset.db_ok}
            for asset in assets[:30]
        ],
        "anomalies": [
            {"hostname": row.hostname or row.host, "trigger": row.trigger_name, "severity": row.severity, "message": row.message, "last_seen_at": row.last_seen_at}
            for row in active_anomalies
        ],
    }
    return payload


@router.get("/api/v1/dashboard/stats", dependencies=[Depends(require_perm("dashboard:read"))])
def dashboard_stats(db: Session = Depends(get_db)):
    """大屏效能统计聚合：MTTR / 自动化闭环 / 工单漏斗 / 告警帕累托 / 时段热力图 / 策略灯归因。

    口径说明：
    - MTTR = 已恢复工单 (closed_at - created_at) 均值（分钟），节省工时按每单人工基线 0.5h 折算
    - 自动化闭环率 = 无审批记录的已恢复工单 / 全部已恢复工单
    - 告警统计窗口为近 7 天（按 first_seen_at），热力图按北京时间 星期×小时 聚合
    """
    now = datetime.now(timezone.utc)
    since7 = now - timedelta(days=7)

    # ===== MTTR / 闭环效能 =====
    recovered = db.scalars(select(Ticket).where(Ticket.status == "recovered")).all()
    durations = sorted(
        (t.closed_at - t.created_at).total_seconds() / 60
        for t in recovered
        if t.closed_at and t.closed_at >= t.created_at
    )
    avg_mttr = round(sum(durations) / len(durations), 1) if durations else 0
    p50_mttr = round(durations[len(durations) // 2], 1) if durations else 0

    # ===== 自动化闭环：有无 Approval 记录区分「全自动 / 审批后恢复」 =====
    approval_ticket_ids = set(db.scalars(select(Approval.ticket_id)).all())
    auto_recovered = sum(1 for t in recovered if t.id not in approval_ticket_ids)
    approved_recovered = len(recovered) - auto_recovered
    closed_total = len(recovered)
    automation_rate = round(auto_recovered / closed_total * 100, 1) if closed_total else 0

    # ===== 工单状态漏斗（8 态计数） =====
    status_rows = db.execute(select(Ticket.status, func.count()).group_by(Ticket.status)).all()
    status_counts = {status: count for status, count in status_rows}
    escalated = status_counts.get("escalated", 0)

    # ===== 策略灯分布 + 红灯归因（升级原因 Top） =====
    light_rows = db.execute(
        select(Ticket.policy_light, func.count()).group_by(Ticket.policy_light)
    ).all()
    lights = {"green": 0, "yellow": 0, "red": 0}
    for light, count in light_rows:
        if light in lights:
            lights[light] = count
    reason_rows = db.execute(
        select(Ticket.escalate_reason, func.count())
        .where(Ticket.status == "escalated")
        .group_by(Ticket.escalate_reason)
        .order_by(func.count().desc())
        .limit(4)
    ).all()

    # ===== 告警近 7 天：帕累托 / 热力图 / 恢复闭环率 =====
    anomalies = db.scalars(
        select(AnomalyEvent).where(AnomalyEvent.first_seen_at >= since7)
    ).all()
    recovered_alerts = sum(1 for a in anomalies if a.status == "recovered")
    host_counter: dict[str, int] = {}
    beijing = timezone(timedelta(hours=8))
    heatmap = [[0] * 24 for _ in range(7)]  # 行=周一~周日（weekday()），列=0~23 时
    for a in anomalies:
        host = a.hostname or a.host or "未知"
        host_counter[host] = host_counter.get(host, 0) + 1
        local = a.first_seen_at.astimezone(beijing)
        heatmap[local.weekday()][local.hour] += 1
    top_assets = [
        {"hostname": host, "count": count}
        for host, count in sorted(host_counter.items(), key=lambda kv: kv[1], reverse=True)[:6]
    ]

    # ===== 全量工单 / 事件 / 审批（表量级小，Python 聚合即可） =====
    all_tickets = db.scalars(select(Ticket)).all()
    events = db.scalars(select(TicketEvent).order_by(TicketEvent.created_at)).all()
    events_by_ticket: dict[int, list[TicketEvent]] = {}
    for ev in events:
        events_by_ticket.setdefault(ev.ticket_id, []).append(ev)

    # ===== MTTA：立案 → 首次开始处置（采证/诊断/执行任一） =====
    mtta_diffs = []
    for t in all_tickets:
        start = next(
            (
                ev.created_at
                for ev in events_by_ticket.get(t.id, [])
                if ev.kind in ("evidence_gathered", "diagnosis_completed", "execution_started")
            ),
            None,
        )
        if start and start >= t.created_at:
            mtta_diffs.append((start - t.created_at).total_seconds() / 60)

    # ===== 各环节滞留时长（小时）：含未完结区间（末端=当前时间，反映实时积压） =====
    def _dwell_hours(start_kind: str, end_kinds: tuple[str, ...]) -> float | None:
        vals = []
        for t in all_tickets:
            start = None
            for ev in events_by_ticket.get(t.id, []):
                if ev.kind == start_kind:
                    start = ev.created_at
                elif start is not None and ev.kind in end_kinds:
                    vals.append((ev.created_at - start).total_seconds() / 3600)
                    start = None
            if start is not None:  # 仍停留在该环节
                vals.append((now - start).total_seconds() / 3600)
        return round(sum(vals) / len(vals), 1) if vals else None

    dwell = {
        "analysis": _dwell_hours("alert_received", ("evidence_gathered", "diagnosis_completed")),
        "approval": _dwell_hours("approval_requested", ("approval_granted", "approval_rejected")),
        "executing": _dwell_hours("execution_started", ("verification_started",)),
        "verifying": _dwell_hours("verification_started", ("recovered", "escalated")),
    }

    # ===== 审批时效：平均等待 / 超时作废 / 在途 / 审批人响应 Top3 =====
    approvals = db.scalars(select(Approval)).all()
    decided = [a for a in approvals if a.decided_at]
    wait_minutes = [(a.decided_at - a.created_at).total_seconds() / 60 for a in decided]
    by_approver: dict[str, list[float]] = {}
    for a in decided:
        if a.approver:
            by_approver.setdefault(a.approver, []).append(
                (a.decided_at - a.created_at).total_seconds() / 60
            )
    approval_payload = {
        "avg_wait_minutes": round(sum(wait_minutes) / len(wait_minutes), 1) if wait_minutes else None,
        "expired": sum(
            1 for a in approvals if a.status == "expired" or (a.status == "pending" and a.expires_at < now)
        ),
        "pending": sum(1 for a in approvals if a.status == "pending" and a.expires_at >= now),
        "approvers": [
            {"approver": name, "count": len(minutes), "avg_minutes": round(sum(minutes) / len(minutes), 1)}
            for name, minutes in sorted(by_approver.items(), key=lambda kv: len(kv[1]), reverse=True)[:3]
        ],
    }

    # ===== 预案执行力：按预案分组 成功/升级/在途 + 冷却命中；验证一次通过率 =====
    try:
        from app.domain.catalog import load_catalog

        catalog = load_catalog()
    except Exception:  # 预案目录缺失时不阻断统计
        catalog = {}
    pb_stats: dict[str, dict] = {}
    kinds_by_ticket = {t.id: {ev.kind for ev in events_by_ticket.get(t.id, [])} for t in all_tickets}

    def _policy_blob(t: Ticket) -> str:
        blob = t.escalate_reason or ""
        if isinstance(t.policy_result, dict):
            blob += " " + " ".join(str(r) for r in (t.policy_result.get("reasons") or []))
        return blob

    for t in all_tickets:
        pid = t.candidate_action_id or t.action_type
        st = pb_stats.setdefault(pid, {"total": 0, "recovered": 0, "escalated": 0, "open": 0, "cooldown": 0})
        st["total"] += 1
        if t.status == "recovered":
            st["recovered"] += 1
        elif t.status == "escalated":
            st["escalated"] += 1
        elif t.status != "skipped":
            st["open"] += 1
        if "冷却" in _policy_blob(t):
            st["cooldown"] += 1
    playbook_items = []
    for pid, st in sorted(pb_stats.items(), key=lambda kv: kv[1]["total"], reverse=True)[:5]:
        closed = st["recovered"] + st["escalated"]
        pb = catalog.get(pid)
        playbook_items.append(
            {
                "id": pid,
                "name": (pb.name if pb else pid),
                **st,
                "success_rate": round(st["recovered"] / closed * 100, 1) if closed else None,
            }
        )
    verified = [t for t in all_tickets if "verification_started" in kinds_by_ticket.get(t.id, set())]
    v_pass = sum(1 for t in verified if t.status == "recovered")
    v_fail = sum(1 for t in verified if t.status == "escalated")
    verify_first_pass = round(v_pass / (v_pass + v_fail) * 100, 1) if (v_pass + v_fail) else None

    # ===== AI 诊断质量：建议采纳率 + 知识盲区（未命中预案的告警类型 Top3） =====
    diagnosed = [t for t in all_tickets if t.diagnosis]
    adopted = sum(1 for t in diagnosed if t.candidate_action_id)
    blind_counter: dict[str, int] = {}
    for t in all_tickets:
        if t.status != "escalated" or not t.trigger_name:
            continue
        if "未命中" in _policy_blob(t):
            blind_counter[t.trigger_name] = blind_counter.get(t.trigger_name, 0) + 1
    blind_spots = [
        {"trigger": trigger, "count": count}
        for trigger, count in sorted(blind_counter.items(), key=lambda kv: kv[1], reverse=True)[:3]
    ]

    # ===== 容量预测：近 3 天磁盘采样线性外推「N 天后写到 95%」 =====
    since3 = now - timedelta(days=3)
    disk_series: dict[str, list[tuple[datetime, float]]] = {}
    for asset_id, ts_, disk in db.execute(
        select(SystemMetricSample.asset_id, SystemMetricSample.ts, SystemMetricSample.disk).where(
            SystemMetricSample.asset_id.isnot(None), SystemMetricSample.ts >= since3
        )
    ).all():
        if disk is not None:
            disk_series.setdefault(asset_id, []).append((ts_, disk))
    for asset_id, ts_, disk in db.execute(
        select(MetricSample.asset_id, MetricSample.ts, MetricSample.disk).where(MetricSample.ts >= since3)
    ).all():
        if disk is not None:
            disk_series.setdefault(asset_id, []).append((ts_, disk))
    assets_all = db.scalars(select(Asset)).all()
    hostname_by_id = {a.id: a.hostname for a in assets_all}
    predictions = []
    for aid, points in disk_series.items():
        points.sort()
        if len(points) < 6:
            continue
        first_disk, last_disk = points[0][1], points[-1][1]
        span_days = max((points[-1][0] - points[0][0]).total_seconds() / 86400, 0.5)
        slope = (last_disk - first_disk) / span_days  # %/天
        if slope <= 0.05 or last_disk >= 95:
            continue
        days = round((95 - last_disk) / slope, 1)
        if days <= 60:
            predictions.append(
                {"asset_id": aid, "hostname": hostname_by_id.get(aid, aid), "disk_now": round(last_disk, 1), "days_to_full": days}
            )
    predictions.sort(key=lambda row: row["days_to_full"])
    days_to_full_by_asset = {row["asset_id"]: row["days_to_full"] for row in predictions}

    # ===== 资产健康分（0-100）：可达/数据库/近期告警/在途工单/磁盘趋势/老化 扣分制 =====
    anomaly_by_asset: dict[str, int] = {}
    for a in anomalies:
        if a.asset_id:
            anomaly_by_asset[a.asset_id] = anomaly_by_asset.get(a.asset_id, 0) + 1
    open_ticket_by_asset: dict[str, int] = {}
    for t in all_tickets:
        if t.status not in ("recovered", "skipped"):
            open_ticket_by_asset[t.asset_id] = open_ticket_by_asset.get(t.asset_id, 0) + 1
    score_map: dict[str, int] = {}
    score_details = []
    aging_hosts = []
    for a in assets_all:
        score, reasons = 100, []
        if not a.reachable:
            score -= 40
            reasons.append("主机不可达")
        if not a.db_ok:
            score -= 15
            reasons.append("数据库探测失败")
        na = anomaly_by_asset.get(a.id, 0)
        if na:
            score -= min(na * 3, 24)
            reasons.append(f"近 7 日告警 {na} 条")
        ot = open_ticket_by_asset.get(a.id, 0)
        if ot:
            score -= min(ot * 8, 24)
            reasons.append(f"在途工单 {ot} 单")
        eta = days_to_full_by_asset.get(a.id)
        if eta is not None:
            score -= 15 if eta < 14 else 8
            reasons.append(f"磁盘约 {eta} 天后写满")
        if a.last_restart_at is None or (now - a.last_restart_at).days > 180:
            score -= 5
            reasons.append("超 180 天未重启")
            aging_hosts.append(a.hostname)
        score = max(score, 5)
        score_map[a.id] = score
        score_details.append(
            {"asset_id": a.id, "kind": a.kind, "hostname": a.hostname, "score": score, "reasons": reasons}
        )
    worst_scores = sorted(score_details, key=lambda row: row["score"])[:5]

    payload = {
        "generated_at": now,
        "mttr": {
            "avg_minutes": avg_mttr,
            "p50_minutes": p50_mttr,
            "recovered_count": closed_total,
            "saved_hours": round(closed_total * 0.5, 1),  # 每单人工基线 0.5h
        },
        "automation": {
            "auto_recovered": auto_recovered,
            "approved_recovered": approved_recovered,
            "escalated": escalated,
            "open": sum(
                count for status, count in status_counts.items() if status not in ("recovered", "skipped")
            ),
            "rate": automation_rate,
        },
        "funnel": {
            "pending_analysis": status_counts.get("pending_analysis", 0),
            "pending_approval": status_counts.get("pending_approval", 0),
            "pending_execution": status_counts.get("pending_execution", 0),
            "executing": status_counts.get("executing", 0),
            "verifying": status_counts.get("verifying", 0),
            "recovered": status_counts.get("recovered", 0),
            "escalated": escalated,
        },
        "policy": {
            **lights,
            "red_reasons": [
                {"reason": (reason or "未记录原因"), "count": count} for reason, count in reason_rows
            ],
        },
        "alerts": {
            "total_7d": len(anomalies),
            "recovered_7d": recovered_alerts,
            "recover_rate": round(recovered_alerts / len(anomalies) * 100, 1) if anomalies else 0,
            "top_assets": top_assets,
            "heatmap": heatmap,
        },
        "mtta": {
            "avg_minutes": round(sum(mtta_diffs) / len(mtta_diffs), 1) if mtta_diffs else None,
            "count": len(mtta_diffs),
        },
        "dwell": dwell,
        "approval": approval_payload,
        "playbooks": {
            "items": playbook_items,
            "cooldown_total": sum(st["cooldown"] for st in pb_stats.values()),
            "verify_first_pass_rate": verify_first_pass,
        },
        "ai": {
            "adoption_rate": round(adopted / len(diagnosed) * 100, 1) if diagnosed else None,
            "diagnosed_count": len(diagnosed),
            "blind_spots": blind_spots,
        },
        "capacity": {
            "predictions": predictions[:3],
            "aging_count": len(aging_hosts),
            "aging_hosts": aging_hosts[:5],
        },
        "scores": {
            "map": score_map,
            "worst": worst_scores,
        },
    }
    return payload


@router.get("/api/v1/dashboard/netdata", dependencies=[Depends(require_perm("dashboard:read"))])
def dashboard_netdata(
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None, ge=1, le=24),
    minutes: int = Query(default=5, ge=1, le=60),
):
    """独立大屏使用的 Netdata 聚合接口。

    每台资产独立降级，某一台 Netdata 离线不会阻断其他资产和既有大屏数据。
    底层是逐台 SSH/HTTP 实时探测，开销大：调用方应控制轮询频率。
    """
    asset_limit = limit or get_settings().netdata_max_assets
    assets = db.scalars(select(Asset).order_by(Asset.hostname).limit(asset_limit)).all()
    if not assets:
        return {"generated_at": datetime.now(timezone.utc), "online_count": 0, "configured_count": 0, "items": [], "featured": None}

    with ThreadPoolExecutor(max_workers=min(6, len(assets))) as pool:
        snapshots = list(pool.map(lambda asset: collect_snapshot(asset, minutes), assets))
    items = [_netdata_summary(snapshot) for snapshot in snapshots]
    featured = next((snapshot for snapshot in snapshots if snapshot.get("online")), None)
    if featured is None:
        featured = next((snapshot for snapshot in snapshots if snapshot.get("configured")), None)
    return {
        "generated_at": datetime.now(timezone.utc),
        "online_count": sum(1 for item in items if item["online"]),
        "configured_count": sum(1 for item in items if item["configured"]),
        "items": items,
        "featured": featured,
    }


@router.get("/api/v1/dashboard/topology", dependencies=[Depends(require_perm("dashboard:read"))])
def dashboard_topology(db: Session = Depends(get_db)):
    """大屏资产关联拓扑：母机 → 子机（含业务分组），供 3D 拓扑面板消费。

    未挂载母机的子机作为孤立节点一并返回（children 为空）。
    """
    assets = db.scalars(select(Asset).order_by(Asset.hostname)).all()
    mothers = [a for a in assets if a.kind == "mother"]
    mother_ids = {m.id for m in mothers}
    children_by_mother: dict[str, list[Asset]] = {}
    standalone: list[Asset] = []
    for a in assets:
        if a.kind != "child":
            continue
        if a.mother_id and a.mother_id in mother_ids:
            children_by_mother.setdefault(a.mother_id, []).append(a)
        else:
            standalone.append(a)

    def _node(a: Asset) -> dict:
        extra = a.extra or {}
        ip = (extra.get("provision") or {}).get("ip") or (extra.get("deploy") or {}).get("ip") or ""
        return {
            "id": a.id,
            "hostname": a.hostname,
            "ip": ip,
            "group": a.group or "未分组",
            "app": a.app,
            "kind": a.kind,
            "reachable": bool(a.reachable),
            "db_ok": bool(a.db_ok),
            "last_seen_at": a.last_seen_at,
        }

    return {
        "generated_at": datetime.now(timezone.utc),
        "groups": sorted({a.group for a in assets if a.group}),
        "mothers": [
            {**_node(m), "children": [_node(c) for c in children_by_mother.get(m.id, [])]}
            for m in mothers
        ] + [{**_node(a), "children": []} for a in standalone],
    }


@router.get("/api/v1/dashboard/assets/{asset_id}/sysinfo", dependencies=[Depends(require_perm("dashboard:read"))])
def dashboard_asset_sysinfo(asset_id: str, db: Session = Depends(get_db)):
    """大屏 3D 拓扑点击服务器 → 读取 Agent 低频上报的全景详情缓存。

    与资产监控「服务器详情」同口径（POST /assets/{id}/sysinfo 的只读快照），
    仅权限口径不同：走 dashboard:read，避免大屏电视账号需要 assets:read。
    """
    if db.get(Asset, asset_id) is None:
        raise HTTPException(404, "资产不存在")

    from app.routers import agent_api as agent_mod

    info = agent_mod.sysinfo_of(asset_id)
    if not info:
        raise HTTPException(
            503,
            "Agent 尚未上报服务器详情（请确认该机已部署 Agent 且在线，稍后重试）",
        )
    info["asset_id"] = asset_id
    return info


@router.get("/api/v1/assets/{asset_id}/netdata", dependencies=[Depends(require_perm("assets:read"))])
def asset_netdata(
    asset_id: str,
    db: Session = Depends(get_db),
    minutes: int = Query(default=5, ge=1, le=60),
):
    """读取单台资产的 Netdata 实时指标。"""
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "资产不存在")
    return collect_snapshot(asset, minutes)
