"""纯代码策略引擎：白名单、风险、前置条件 → 绿灯 / 黄灯 / 红灯。

LLM 不参与本模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import get_settings
from app.domain.catalog import Playbook, get_playbook
from app.domain.safety import cooldown_violated
from app.schemas import Diagnosis, PolicyResult
from app.services.cooldowns import action_fail_cooldown_violated


@dataclass
class AssetContext:
    asset_id: str
    tenant_id: str
    reachable: bool
    db_ok: bool
    last_restart_at: datetime | None
    in_maintenance: bool = False
    role: str = "app"
    action_failed_at: datetime | None = None


def evaluate_policy(
    diagnosis: Diagnosis,
    asset: AssetContext,
    *,
    now: datetime | None = None,
    playbook: Playbook | None = None,
) -> PolicyResult:
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    if asset.in_maintenance:
        return PolicyResult(
            light="red",
            reasons=["资产处于计划维护窗口，禁止自动处置"],
            action_id=None,
            require_approval=False,
            can_execute=False,
            policy_version=settings.policy_version,
        )

    action_id = diagnosis.candidate_action_id
    pb = playbook or (get_playbook(action_id) if action_id else None)
    if not action_id or pb is None:
        return PolicyResult(
            light="red",
            reasons=["手册/预案库未命中该根因，转人工并附带全量证据"],
            action_id=action_id,
            require_approval=False,
            can_execute=False,
            policy_version=settings.policy_version,
        )

    if pb.requires_reachable and not asset.reachable:
        reasons.append("前置条件失败：主机不可达")
    if pb.requires_db_ok and not asset.db_ok:
        reasons.append("前置条件失败：依赖数据库异常，禁止重启应用")
    if pb.restart_cooldown and cooldown_violated(asset.last_restart_at, now):
        reasons.append("前置条件失败：30 分钟内已执行过重启，禁止循环重启")
    if action_fail_cooldown_violated(asset.action_failed_at, now):
        reasons.append("前置条件失败：同资产同动作失败后处于冷却期，禁止自动再试")

    if reasons:
        return PolicyResult(
            light="red",
            reasons=reasons,
            action_id=pb.id,
            require_approval=False,
            can_execute=False,
            policy_version=settings.policy_version,
        )

    high_risk = pb.risk == "high" or not pb.auto_allowed
    if high_risk:
        return PolicyResult(
            light="yellow",
            reasons=["高风险动作，需人工审批（绑定资产 + 作业版本 + 参数摘要）"],
            action_id=pb.id,
            require_approval=True,
            can_execute=False,
            policy_version=settings.policy_version,
        )

    return PolicyResult(
        light="green",
        reasons=["低风险 + 白名单 + 前置条件全部满足，允许自动执行"],
        action_id=pb.id,
        require_approval=False,
        can_execute=True,
        policy_version=settings.policy_version,
    )
