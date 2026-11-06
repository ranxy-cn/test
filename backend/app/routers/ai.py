"""AI 日志分析路由：配置管理（加密存储/连接测试）、分析结果查询、人工处理、手动重分析。

权限点：ai:view 查看 / ai:config 配置维护 / ai:feedback 人工处理与重分析。
安全：api_key 落库前 Fernet 加密，读取一律脱敏；所有动作写 AiAuditLog。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, select, func, cast
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AiAnalysis, AiAuditLog, AnomalyEvent, utcnow
from app.routers.deps import CurrentUser, require_perm
from app.schemas import (
    AiAnalysisOut,
    AiConnectionTestOut,
    AiFeedbackIn,
    AiSettingsIn,
    AiSettingsOut,
)
from app.services import ai_analyzer

router = APIRouter()


@router.get("/api/v1/ai/settings", response_model=AiSettingsOut)
def get_settings_view(db: Session = Depends(get_db), _: CurrentUser = Depends(require_perm("ai:config"))):
    return AiSettingsOut(**ai_analyzer.masked_config(ai_analyzer.get_ai_config(db)))


@router.put("/api/v1/ai/settings", response_model=AiSettingsOut)
def save_settings_view(
    body: AiSettingsIn,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require_perm("ai:config")),
):
    masked = ai_analyzer.save_ai_config(
        db, body.model_dump(), operator=current.username
    )
    return AiSettingsOut(**masked)


@router.post("/api/v1/ai/settings/test", response_model=AiConnectionTestOut)
def test_connection_view(
    body: AiSettingsIn | None = None,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require_perm("ai:config")),
):
    """连接测试：用「请求体临时参数优先，否则已存配置」发一次最小请求。"""
    cfg = ai_analyzer.get_ai_config(db)
    if body is not None and (body.base_url or body.api_key or body.model):
        if body.base_url:
            cfg["base_url"] = body.base_url.rstrip("/")
        if body.api_key:
            cfg["api_key"] = body.api_key
        if body.model:
            cfg["model"] = body.model
    if not cfg.get("base_url"):
        db.add(
            AiAuditLog(action="connection_test", operator=current.username, ok=False,
                       model=cfg.get("model") or "", detail={"error": "未配置 API 端点 URL"})
        )
        db.commit()
        return AiConnectionTestOut(ok=False, message="未配置 API 端点 URL")
    if not cfg.get("api_key"):
        db.add(
            AiAuditLog(action="connection_test", operator=current.username, ok=False,
                       model=cfg.get("model") or "", detail={"error": "未配置 API 密钥"})
        )
        db.commit()
        return AiConnectionTestOut(ok=False, message="未配置 API 密钥")
    try:
        probe = dict(cfg)
        probe["timeout_seconds"] = min(int(cfg.get("timeout_seconds") or 60), 30)
        _, model, latency = ai_analyzer._chat(
            probe, "连接测试：请输出 {\"summary\": \"ok\"}"
        )
    except Exception as exc:
        db.add(
            AiAuditLog(action="connection_test", operator=current.username, ok=False,
                       model=cfg.get("model") or "", detail={"error": str(exc)[:300]})
        )
        db.commit()
        return AiConnectionTestOut(ok=False, message=f"连接失败：{str(exc)[:300]}")
    db.add(
        AiAuditLog(action="connection_test", operator=current.username, ok=True,
                   model=model, latency_ms=latency, detail={"base_url": cfg.get("base_url")})
    )
    db.commit()
    return AiConnectionTestOut(ok=True, message="连接成功", model=model, latency_ms=latency)


@router.get("/api/v1/ai/analyses")
def list_analyses_view(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    anomaly_id: int | None = None,
    status: str | None = None,
    severity: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    order: str = Query("time", pattern="^(time|severity)$"),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_perm("ai:view")),
):
    """分析结果列表：按时间倒序；order=severity 时按 AI 判定严重程度+时间排序（相关性排序）。"""
    stmt = select(AiAnalysis)
    if anomaly_id:
        stmt = stmt.where(AiAnalysis.anomaly_id == anomaly_id)
    if status:
        stmt = stmt.where(AiAnalysis.status == status)
    if severity:
        stmt = stmt.where(AiAnalysis.severity == severity)
    if tag:
        stmt = stmt.where(cast(AiAnalysis.solutions, String).like(f'%"{tag}"%'))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            AiAnalysis.summary.like(like) | AiAnalysis.diagnosis.like(like) | AiAnalysis.trigger_like(like)
            if hasattr(AiAnalysis, "trigger_like")
            else AiAnalysis.summary.like(like) | AiAnalysis.diagnosis.like(like)
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    sev_rank = func.field  # 占位，下方按方言安全实现
    if order == "severity":
        severity_order = func.substring_index  # MySQL 专用会破坏 sqlite；改为 Python 排序权重表
        rows = db.scalars(stmt.order_by(AiAnalysis.created_at.desc()).limit(page_size * 5).offset(0)).all()
        rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        rows.sort(key=lambda r: (rank.get(r.severity, 5), -(r.created_at.timestamp() if r.created_at else 0)))
        rows = rows[(page - 1) * page_size: (page - 1) * page_size + page_size]
    else:
        rows = db.scalars(
            stmt.order_by(AiAnalysis.created_at.desc(), AiAnalysis.id.desc())
            .limit(page_size).offset((page - 1) * page_size)
        ).all()

    # 关联告警摘要（双向关联：列表直接给出告警信息便于跳转）
    ids = {r.anomaly_id for r in rows}
    anomalies = {}
    if ids:
        for ev in db.scalars(select(AnomalyEvent).where(AnomalyEvent.id.in_(ids))).all():
            anomalies[ev.id] = {
                "id": ev.id, "hostname": ev.hostname or ev.host, "ip": ev.ip,
                "trigger_name": ev.trigger_name, "status": ev.status, "severity": ev.severity,
                "ai_status": ev.ai_status,
            }
    return {
        "total": total,
        "items": [AiAnalysisOut.model_validate(r).model_dump() for r in rows],
        "anomalies": anomalies,
    }


@router.get("/api/v1/ai/analyses/{analysis_id}")
def get_analysis_view(
    analysis_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_perm("ai:view")),
):
    row = db.get(AiAnalysis, analysis_id)
    if row is None:
        raise HTTPException(404, "分析结果不存在")
    ev = db.get(AnomalyEvent, row.anomaly_id)
    out = AiAnalysisOut.model_validate(row).model_dump()
    out["raw_response"] = row.raw_response or ""
    out["context_digest"] = row.context_digest
    out["anomaly"] = None
    if ev is not None:
        out["anomaly"] = {
            "id": ev.id, "event_id": ev.event_id, "hostname": ev.hostname or ev.host,
            "ip": ev.ip, "trigger_name": ev.trigger_name, "message": ev.message,
            "severity": ev.severity, "status": ev.status, "first_seen_at": ev.first_seen_at,
            "last_seen_at": ev.last_seen_at,
        }
    return out


@router.post("/api/v1/ai/analyses/{analysis_id}/feedback")
def feedback_analysis_view(
    analysis_id: int,
    body: AiFeedbackIn,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require_perm("ai:feedback")),
):
    """人工处理记录：处理人 + 备注，写审计。"""
    row = db.get(AiAnalysis, analysis_id)
    if row is None:
        raise HTTPException(404, "分析结果不存在")
    row.handled_by = current.username
    row.handled_note = body.note
    row.handled_at = utcnow()
    db.add(
        AiAuditLog(anomaly_id=row.anomaly_id, analysis_id=row.id, action="feedback",
                   operator=current.username, ok=True, detail={"note": body.note[:500]})
    )
    db.commit()
    return {"ok": True, "id": row.id, "handled_by": row.handled_by, "handled_at": row.handled_at}


@router.post("/api/v1/ai/anomalies/{anomaly_id}/analyze")
def trigger_analysis_view(
    anomaly_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require_perm("ai:feedback")),
):
    """手动触发（重新）分析：跳过进行中幂等，直接派发异步任务。"""
    ev = db.get(AnomalyEvent, anomaly_id)
    if ev is None:
        raise HTTPException(404, "告警不存在")
    cfg = ai_analyzer.get_ai_config(db)
    if not cfg.get("enabled"):
        raise HTTPException(400, "AI 分析未启用，请先在系统管理→AI 服务配置中开启")
    ev.ai_status = "pending"
    db.add(
        AiAuditLog(anomaly_id=anomaly_id, action="trigger", operator=current.username,
                   ok=True, detail={"source": "manual"})
    )
    db.commit()
    _dispatch(anomaly_id, ai_analyzer.priority_for(ev))
    return {"ok": True, "anomaly_id": anomaly_id, "ai_status": ev.ai_status}


def _dispatch(anomaly_id: int, priority: int) -> None:
    """派发异步分析任务：USE_CELERY=true 走默认队列（Redis priority 分级），否则同步执行（本地/测试）。"""
    from app.config import get_settings
    from app.workers.tasks import analyze_anomaly_logs

    if get_settings().use_celery:
        analyze_anomaly_logs.apply_async(args=[anomaly_id], priority=priority)
    else:
        analyze_anomaly_logs.run(anomaly_id)
