"""告警策略：CPU/内存/负载的阈值与触发窗口（平台纯存储）。

策略优先级：子机自有 extra["alert_policy"] > 所属母机 extra["alert_policy"] > 平台默认。
母机策略保存于母机资产 extra["alert_policy"]；本地判定引擎（alert_engine）按
生效策略对 agent 上报指标做「越限持续满窗口」判定；不再同步到任何外部监控系统。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Asset

DEFAULT_POLICY: dict[str, float] = {
    "cpu_threshold": 90,
    "cpu_window_minutes": 5,
    "mem_threshold": 90,
    "mem_window_minutes": 5,
    "load_threshold": 1.5,
    "load_window_minutes": 5,
}


def normalize_policy(data: dict | None) -> dict:
    """校验并规范化策略字段；缺失字段补默认值，非法值抛 ValueError。"""
    policy = dict(DEFAULT_POLICY)
    for key, value in (data or {}).items():
        if key in policy:
            policy[key] = value
    for name in ("cpu_threshold", "mem_threshold", "cpu_window_minutes", "mem_window_minutes", "load_window_minutes"):
        try:
            policy[name] = int(policy[name])
        except (TypeError, ValueError):
            raise ValueError(f"告警策略字段 {name} 必须是整数") from None
    try:
        policy["load_threshold"] = round(float(policy["load_threshold"]), 2)
    except (TypeError, ValueError):
        raise ValueError("告警策略字段 load_threshold 必须是数字") from None
    for name in ("cpu_threshold", "mem_threshold"):
        if not 1 <= policy[name] <= 99:
            raise ValueError(f"{name} 取值范围 1~99")
    if not 0.1 <= policy["load_threshold"] <= 100:
        raise ValueError("load_threshold 取值范围 0.1~100")
    for name in ("cpu_window_minutes", "mem_window_minutes", "load_window_minutes"):
        if not 1 <= policy[name] <= 120:
            raise ValueError(f"{name} 取值范围 1~120 分钟")
    return policy


def effective_policy(db: Session, asset) -> tuple[dict, str]:
    """资产生效策略：返回 (策略, 来源)。

    来源 self=子机自有策略 / mother=继承所属母机 / default=平台默认。
    """
    own = (asset.extra or {}).get("alert_policy")
    if own:
        return normalize_policy(own), "self"
    if asset.kind == "child":
        mother = db.get(Asset, asset.mother_id) if asset.mother_id else None
        if mother is not None and (mother.extra or {}).get("alert_policy"):
            return normalize_policy(mother.extra["alert_policy"]), "mother"
    return dict(DEFAULT_POLICY), "default"
