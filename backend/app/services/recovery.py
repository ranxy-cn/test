"""告警恢复任务服务。

告警触发 → ensure_for_anomaly：按 event_id 幂等生成恢复任务；
命中启用中的恢复脚本（rule_key 精确匹配优先，其次通配）时：
- low 风险：告警触发后后台线程自动 SSH 执行（executed_by=system）
- high 风险：生成任务等人工在任务单页确认执行
告警恢复 → resolve_for_anomaly：未结任务自动关闭（done）——「恢复时消失」。
告警持续未恢复再次触发 → priority 叠加升级（P 级基础分 + 每次触发 +5，封顶 99）。

脚本执行走 paramiko SSH（与 diagnostics 快照同通道：先公钥后密码），
输出留痕 execute_output，供任务单页查看与审计。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import paramiko
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Asset, AnomalyEvent, RecoveryScript, RecoveryTask, utcnow
from app.services.inspector import INSPECT_KEY_PATH, _load_private_key

log = logging.getLogger("devops.recovery")

# P 级 → 优先级基础分（越大越紧急）
SEVERITY_BASE = {"P0": 90, "P1": 70, "P2": 50, "P3": 30}
# 再次触发叠加步长 / 封顶
ESCALATE_STEP = 5
MAX_PRIORITY = 99

OPEN_STATUSES = ("open", "executing")


def rule_key_of(anomaly: AnomalyEvent) -> str:
    """告警规则 key：引擎事件取 payload.rule_id / metric，webhook 事件取 trigger_name 兜底空。"""
    payload = anomaly.payload or {}
    return str(payload.get("rule_id") or payload.get("metric") or "")[:64]


def _pick_script(db: Session, rule_key: str) -> RecoveryScript | None:
    """选脚本：精确匹配 rule_key 优先，其次空 rule_key（通配全部）；均取最早创建的启用项。"""
    for rk in (rule_key, ""):
        script = db.scalars(
            select(RecoveryScript)
            .where(RecoveryScript.enabled.is_(True), RecoveryScript.rule_key == rk)
            .order_by(RecoveryScript.id.asc())
        ).first()
        if script is not None:
            return script
    return None


def ensure_for_anomaly(db: Session, anomaly: AnomalyEvent) -> RecoveryTask | None:
    """告警触发生成/升级恢复任务（调用方负责 commit）。

    幂等：同 event_id 已有未结任务 → 仅叠加优先级（持续未恢复的紧急度升级）。
    """
    rule_key = rule_key_of(anomaly)
    task = db.scalars(
        select(RecoveryTask)
        .where(RecoveryTask.event_id == anomaly.event_id, RecoveryTask.status.in_(OPEN_STATUSES))
        .order_by(RecoveryTask.id.desc())
    ).first()
    if task is not None:
        task.priority = min(int(task.priority or 0) + ESCALATE_STEP, MAX_PRIORITY)
        task.severity = anomaly.severity or task.severity
        db.flush()
        log.info("恢复任务优先级叠加 task_id=%s priority=%s", task.id, task.priority)
        return task

    script = _pick_script(db, rule_key)
    task = RecoveryTask(
        anomaly_id=anomaly.id,
        event_id=anomaly.event_id,
        asset_id=anomaly.asset_id or "",
        rule_key=rule_key,
        severity=anomaly.severity or "",
        priority=SEVERITY_BASE.get(anomaly.severity or "", 50),
        status="open",
        script_id=script.id if script else None,
        script_name=script.name if script else "",
    )
    db.add(task)
    db.flush()
    log.info(
        "恢复任务生成 task_id=%s event=%s rule=%s script=%s risk=%s",
        task.id, anomaly.event_id, rule_key, script.name if script else "-", script.risk_level if script else "-",
    )
    # 低风险脚本：自动执行（后台线程，不阻塞告警扫描/webhook 应答）
    if script is not None and script.risk_level == "low":
        threading.Thread(target=_auto_execute, args=(task.id,), daemon=True).start()
    return task


def resolve_for_anomaly(db: Session, event_id: str, reason: str = "告警已恢复，自动关闭") -> int:
    """告警恢复：未结任务全部自动关闭，返回关闭数（调用方负责 commit）。"""
    tasks = db.scalars(
        select(RecoveryTask).where(RecoveryTask.event_id == event_id, RecoveryTask.status.in_(OPEN_STATUSES))
    ).all()
    now = utcnow()
    for t in tasks:
        t.status = "done"
        t.resolved_at = now
        t.resolve_reason = reason[:128]
    if tasks:
        log.info("恢复任务自动关闭 count=%s event=%s", len(tasks), event_id)
    return len(tasks)


# ---------------------------------------------------------------------------
# 脚本执行（SSH）
# ---------------------------------------------------------------------------

def _resolve_asset_target(db: Session, asset_id: str) -> dict[str, Any] | None:
    """资产 SSH 目标：extra.provision（ip/port/username/password），无密码用全局私钥/兜底密码。"""
    asset = db.get(Asset, asset_id) if asset_id else None
    if asset is None:
        return None
    provision = (asset.extra or {}).get("provision") or {}
    ip = provision.get("ip") or ((asset.extra or {}).get("ssh_host") or "")
    if not ip:
        return None
    return {
        "ip": ip,
        "port": int(provision.get("port") or 22),
        "username": provision.get("username") or "root",
        "password": provision.get("password") or get_settings().diag_ssh_password,
        "asset": asset.hostname,
    }


def _ssh_exec(target: dict[str, Any], command: str, timeout: int) -> tuple[bool, str]:
    """SSH 执行恢复命令，返回 (ok, 输出留痕)。先公钥后密码（与诊断快照同策略）。"""
    pkey = _load_private_key(INSPECT_KEY_PATH)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            target["ip"],
            port=target["port"],
            username=target["username"],
            password=target["password"] or None,
            pkey=pkey,
            timeout=8.0,
            banner_timeout=8.0,
            auth_timeout=8.0,
            allow_agent=False,
            look_for_keys=False,
        )
        _, stdout, stderr = client.exec_command(command, timeout=max(10, int(timeout)))
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        text = (out + (f"\n[stderr]\n{err}" if err.strip() else "")).strip()
        return code == 0, text[:8000] or f"(exit={code}, 无输出)"
    finally:
        client.close()


def execute_task(task_id: int, operator: str = "") -> dict[str, Any]:
    """执行恢复任务绑定的脚本（自建 Session；同步调用，路由/线程均可）。"""
    db = SessionLocal()
    try:
        task = db.get(RecoveryTask, task_id)
        if task is None:
            return {"ok": False, "output": "任务不存在"}
        if task.status not in OPEN_STATUSES:
            return {"ok": False, "output": f"任务已结（{task.status}），无需执行"}
        script = db.get(RecoveryScript, task.script_id) if task.script_id else None
        if script is None or not script.command:
            return {"ok": False, "output": "任务未绑定恢复脚本（纯人工任务），请人工处理后关闭"}
        target = _resolve_asset_target(db, task.asset_id)
        if target is None:
            task.execute_ok = False
            task.execute_output = "无法确定 SSH 目标：资产未录入 SSH 信息（extra.provision）"
            task.executed_by = operator or "system"
            db.commit()
            return {"ok": False, "output": task.execute_output}

        task.status = "executing"
        task.executed_by = operator or "system"
        db.commit()
        try:
            ok, output = _ssh_exec(target, script.command, script.timeout_seconds)
        except Exception as exc:
            log.exception("恢复脚本执行失败 task_id=%s", task_id)
            ok, output = False, f"SSH 执行异常：{exc}"[:2000]
        task = db.get(RecoveryTask, task_id)
        task.execute_ok = ok
        task.execute_output = output
        if ok:
            task.status = "done"
            task.resolved_at = utcnow()
            task.resolve_reason = f"脚本「{script.name}」执行成功"[:128]
        else:
            task.status = "open"  # 失败回 open，等待人工介入/重试
        db.commit()
        log.info("恢复脚本执行完成 task_id=%s ok=%s by=%s", task_id, ok, task.executed_by)
        return {"ok": ok, "output": output}
    finally:
        db.close()


def _auto_execute(task_id: int) -> None:
    """低风险脚本自动执行入口（后台线程）。"""
    try:
        execute_task(task_id, operator="system")
    except Exception:
        log.exception("恢复任务自动执行异常 task_id=%s", task_id)
