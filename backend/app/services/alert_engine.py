"""指标越限告警引擎：对自研 Agent 上报的系统指标做本地越限判定，产出站内异常事件。

判定语义：「越限持续满窗口」——触发窗口（按各资产生效策略，子机自有 > 母机继承 > 默认）
内全部样本均越过阈值且样本数 ≥ 2，视为持续越限；瞬间冲高回落不触发。
事件写入 anomaly_events（event_id=metric-<asset_id>-<指标> 幂等），自动出现在
异常列表 / 母机总览卡片红点 / 工作台，由 celery beat 每分钟调用 run_alert_cycle。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnomalyEvent, Asset, SystemMetricSample, utcnow
from app.services.alert_policy import effective_policy

# (样本字段, 阈值字段, 窗口字段, 展示名, 单位)；三项均为「越高越坏」
METRIC_DEFS: list[tuple[str, str, str, str, str]] = [
    ("cpu", "cpu_threshold", "cpu_window_minutes", "CPU 使用率", "%"),
    ("mem", "mem_threshold", "mem_window_minutes", "内存使用率", "%"),
    ("load1", "load_threshold", "load_window_minutes", "1分钟负载", ""),
]


def run_alert_cycle(db: Session) -> dict:
    """扫描全部子机：越限持续满窗口 → 建事件；不再越限 → 自动恢复。返回统计。"""
    now = utcnow()
    stats = {"checked": 0, "triggered": 0, "recovered": 0}
    children = db.scalars(select(Asset).where(Asset.kind == "child")).all()
    for asset in children:
        stats["checked"] += 1
        policy, source = effective_policy(db, asset)
        max_win = max(int(policy["cpu_window_minutes"]), int(policy["mem_window_minutes"]), int(policy["load_window_minutes"]))
        rows = db.scalars(
            select(SystemMetricSample).where(
                SystemMetricSample.asset_id == asset.id,
                SystemMetricSample.ts >= now - timedelta(minutes=max_win),
            )
        ).all()
        provision = (asset.extra or {}).get("provision") or {}
        ip = str(provision.get("ip") or "")
        for field, th_key, win_key, label, unit in METRIC_DEFS:
            threshold = policy[th_key]
            win = int(policy[win_key])
            values = [
                getattr(r, field)
                for r in rows
                if r.ts >= now - timedelta(minutes=win) and getattr(r, field) is not None
            ]
            breach = len(values) >= 2 and all(v >= threshold for v in values)
            latest = values[-1] if values else None
            event_id = f"metric-{asset.id}-{field}"
            ev = db.scalar(select(AnomalyEvent).where(AnomalyEvent.event_id == event_id))
            payload = {
                "kind": "metric_breach",
                "metric": field,
                "label": label,
                "threshold": threshold,
                "window_minutes": win,
                "samples": len(values),
                "latest": latest,
                "policy_source": source,
            }
            if breach:
                if ev is None:
                    db.add(
                        AnomalyEvent(
                            event_id=event_id,
                            hostid="",
                            host=asset.id,
                            hostname=asset.hostname or asset.id,
                            ip=ip,
                            trigger_name=f"{label}超过 {threshold}{unit}（持续 {win} 分钟）",
                            severity="high",
                            message=f"{asset.hostname or asset.id} {label} {latest}{unit} ≥ {threshold}{unit}，已持续约 {win} 分钟",
                            status="abnormal",
                            asset_id=asset.id,
                            payload=payload,
                        )
                    )
                    stats["triggered"] += 1
                elif ev.status != "abnormal":
                    # 曾恢复后再次越限：重新打开同一事件
                    ev.status = "abnormal"
                    ev.recovered_at = None
                    ev.last_seen_at = now
                    ev.payload = payload
                    stats["triggered"] += 1
                else:
                    # 持续越限：仅刷新观测值与时间戳，不重复建事件
                    ev.last_seen_at = now
                    ev.payload = {**(ev.payload or {}), "latest": latest, "samples": len(values)}
            elif ev is not None and ev.status == "abnormal":
                ev.status = "recovered"
                ev.recovered_at = now
                ev.payload = {**(ev.payload or {}), "latest": latest, "recovered": "窗口内不再越限"}
                stats["recovered"] += 1
    db.commit()
    return stats
