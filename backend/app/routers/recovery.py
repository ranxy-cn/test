"""告警恢复任务路由：任务列表/人工执行/取消 + 恢复脚本 CRUD。

权限点：recovery:view 查看 / recovery:execute 执行与取消 / recovery:manage 脚本维护。
脚本执行走 SSH（服务层 recovery.execute_task 同步执行，FastAPI 线程池承载）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AnomalyEvent, RecoveryScript, RecoveryTask, utcnow
from app.routers.deps import CurrentUser, require_perm
from app.services import recovery as recovery_svc

router = APIRouter()


def _task_out(t: RecoveryTask) -> dict:
    return {
        "id": t.id,
        "anomaly_id": t.anomaly_id,
        "event_id": t.event_id,
        "asset_id": t.asset_id,
        "rule_key": t.rule_key,
        "severity": t.severity,
        "priority": t.priority,
        "status": t.status,
        "script_id": t.script_id,
        "script_name": t.script_name,
        "executed_by": t.executed_by,
        "execute_ok": t.execute_ok,
        "execute_output": t.execute_output or "",
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "resolve_reason": t.resolve_reason,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("/api/v1/recovery/tasks")
def list_tasks(
    status: str = Query("open", description="open/executing/done/cancelled/all，默认仅未结任务"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_perm("recovery:view")),
):
    """恢复任务列表：默认只看未结（open/executing）——告警恢复后任务自动关闭即「消失」。"""
    q = select(RecoveryTask)
    if status and status != "all":
        q = q.where(RecoveryTask.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.order_by(RecoveryTask.priority.desc(), RecoveryTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"total": total, "items": [_task_out(t) for t in rows]}


@router.post("/api/v1/recovery/tasks/{task_id}/execute")
def execute_task(
    task_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(require_perm("recovery:execute")),
):
    """人工执行任务绑定的恢复脚本（高风险脚本确认入口；低风险由系统自动执行）。"""
    task = db.get(RecoveryTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    result = recovery_svc.execute_task(task_id, operator=current.username)
    return result


@router.post("/api/v1/recovery/tasks/{task_id}/cancel")
def cancel_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_perm("recovery:execute")),
):
    """人工取消任务（告警仍异常时明确不处理）。"""
    task = db.get(RecoveryTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if task.status not in recovery_svc.OPEN_STATUSES:
        raise HTTPException(400, f"任务已结（{task.status}），无法取消")
    task.status = "cancelled"
    task.resolved_at = utcnow()
    task.resolve_reason = "人工取消"
    db.commit()
    return _task_out(task)


# ---------------------------------------------------------------------------
# 恢复脚本 CRUD
# ---------------------------------------------------------------------------

class ScriptIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=256)
    rule_key: str = Field(default="", max_length=64, description="匹配告警规则 key，空=全部")
    risk_level: str = Field(default="low", pattern="^(low|high)$")
    command: str = Field(min_length=1)
    timeout_seconds: int = Field(default=60, ge=5, le=1800)
    enabled: bool = True


@router.get("/api/v1/recovery/scripts")
def list_scripts(
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_perm("recovery:view")),
):
    rows = db.scalars(select(RecoveryScript).order_by(RecoveryScript.id.asc())).all()
    return {
        "items": [
            {
                "id": s.id, "name": s.name, "description": s.description,
                "rule_key": s.rule_key, "risk_level": s.risk_level,
                "command": s.command, "timeout_seconds": s.timeout_seconds,
                "enabled": s.enabled,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in rows
        ]
    }


@router.post("/api/v1/recovery/scripts")
def create_script(
    body: ScriptIn,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_perm("recovery:manage")),
):
    s = RecoveryScript(**body.model_dump())
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.put("/api/v1/recovery/scripts/{script_id}")
def update_script(
    script_id: int,
    body: ScriptIn,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_perm("recovery:manage")),
):
    s = db.get(RecoveryScript, script_id)
    if s is None:
        raise HTTPException(404, "脚本不存在")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    db.commit()
    return {"id": s.id}


@router.delete("/api/v1/recovery/scripts/{script_id}")
def delete_script(
    script_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_perm("recovery:manage")),
):
    s = db.get(RecoveryScript, script_id)
    if s is None:
        raise HTTPException(404, "脚本不存在")
    in_use = db.scalar(
        select(func.count()).select_from(RecoveryTask).where(RecoveryTask.script_id == script_id)
    ) or 0
    if in_use:
        raise HTTPException(400, f"已有 {in_use} 个恢复任务引用该脚本，请改为停用（enabled=false）")
    db.delete(s)
    db.commit()
    return {"ok": True}
