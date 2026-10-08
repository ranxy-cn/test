"""指标越限告警引擎 v2：对自研 Agent 上报的系统/应用/数据库指标做本地规则判定。

判定语义：「越限持续满窗口」——触发窗口内全部样本均越过阈值且样本数 ≥ 2，
视为持续越限；瞬间冲高回落不触发。事件型规则（OOM/进程消失/端口探活失败）
为「窗口内出现过即异常、整窗干净自动恢复」。
级别体系 P0~P3（severity 直接存储级别值，前端按级别着色：P0 红/P1 橙/P2 黄/P3 蓝灰）。
事件写入 anomaly_events（event_id=metric-<asset_id>-<rule_key> 幂等），由
celery beat 每分钟调用 run_alert_cycle。规则来源：资产生效策略（子机自有 >
母机继承 > 平台默认），策略变更后引擎下轮扫描即用新策略，无需重启。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnomalyEvent, Asset, SystemMetricSample, utcnow
from app.services.alert_policy import RULE_CATALOG_BY_ID, effective_policy

log = logging.getLogger("devops.alert_engine")

# 基础指标：(样本字段, 阈值key, 窗口key, 级别key, 展示名, 单位)；均为「越高越坏」
BASE_METRICS: list[tuple[str, str, str, str, str, str]] = [
    ("cpu", "cpu_threshold", "cpu_window_seconds", "cpu_level", "CPU 使用率", "%"),
    ("mem", "mem_threshold", "mem_window_seconds", "mem_level", "内存使用率", "%"),
    ("load1", "load_threshold", "load_window_seconds", "load_level", "1分钟负载", ""),
]

# 系统扩展指标（样本 ext 列）：(ext字段, 开关key, 阈值key, 窗口key, 级别key, 展示名, 单位)
EXT_METRICS: list[tuple[str, str, str, str, str, str, str]] = [
    ("swap", "swap_enabled", "swap_threshold", "swap_window_seconds", "swap_level", "Swap 使用率", "%"),
    ("inode", "inode_enabled", "inode_threshold", "inode_window_seconds", "inode_level", "Inode 使用率", "%"),
    ("await_ms", "disk_io_enabled", "disk_io_threshold", "disk_io_window_seconds", "disk_io_level", "磁盘 IO 延迟(await)", "ms"),
    ("loss_pct", "net_perf_enabled", "net_loss_threshold", "net_perf_window_seconds", "net_perf_level", "网络丢包率", "%"),
    ("latency_ms", "net_perf_enabled", "net_latency_threshold", "net_perf_window_seconds", "net_perf_level", "网络延迟", "ms"),
    ("bw_rx_pct", "bandwidth_enabled", "bandwidth_threshold", "bandwidth_window_seconds", "bandwidth_level", "入口带宽使用率", "%"),
    ("bw_tx_pct", "bandwidth_enabled", "bandwidth_threshold", "bandwidth_window_seconds", "bandwidth_level", "出口带宽使用率", "%"),
    ("tcp_tw", "tcp_conn_enabled", "tcp_time_wait_threshold", "tcp_conn_window_seconds", "tcp_conn_level", "TIME_WAIT 连接数", "个"),
    ("tcp_conn_pct", "tcp_conn_enabled", "tcp_conn_pct_threshold", "tcp_conn_window_seconds", "tcp_conn_level", "TCP 连接数/上限", "%"),
]

# 单行查询上限：600 样本（5s 间隔约覆盖 50 分钟窗口，满足规范最大 10 分钟窗口）
_MAX_ROWS = 600


def _breach(op: str, v: float, th: float) -> bool:
    if op == "gt":
        return v > th
    if op == "gte":
        return v >= th
    if op == "lt":
        return v < th
    if op == "lte":
        return v <= th
    return False


def _fmt(v) -> str:
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def _upsert_event(
    db: Session,
    stats: dict,
    now,
    asset: Asset,
    ip: str,
    *,
    rule_key: str,
    label: str,
    unit: str,
    level: str,
    enabled: bool,
    breach: bool,
    latest,
    win: int,
    threshold=None,
    op: str = "gte",
    samples: int = 0,
    detail: str = "",
    policy_source: str = "default",
    metric: str = "",
    notify_minutes: int = 0,
    window_series: list | None = None,
    extra: dict | None = None,
) -> None:
    """统一事件写入：breach 建/重开/刷新事件；不越限或规则禁用时自动恢复。

    重复提醒：事件持续 abnormal 期间，每满 notify_minutes 分钟在原事件上
    重复提醒一次（repeat_count +1、last_notified_at 刷新），不产生新事件；
    notify_minutes=0 时仅「触发 / 恢复后再次触发」这类状态变化时提醒（瞬时事件）。

    上下文增强：payload 携带 window_series（窗口内样本值序列 [(ts, val)]，最多
    60 点，便于回看告警前后曲线）与 extra（规则特有上下文，如 OOM 被杀进程明细）。
    首次触发/恢复后再次触发时登记 diag_ids，扫描提交后自动 SSH 采集进程快照。
    """
    event_id = f"metric-{asset.id}-{rule_key}"
    ev = db.scalar(select(AnomalyEvent).where(AnomalyEvent.event_id == event_id))
    payload = {
        "kind": "rule_breach",
        "rule_id": rule_key,
        "metric": metric,
        "label": label,
        "level": level,
        "op": op,
        "threshold": threshold,
        "window_seconds": win,
        "samples": samples,
        "latest": latest,
        "policy_source": policy_source,
        "window_series": list(window_series or [])[-60:],
        **(extra or {}),
    }
    if (not enabled) or (not breach):
        if ev is not None and ev.status == "abnormal":
            ev.status = "recovered"
            ev.recovered_at = now
            ev.payload = {**(ev.payload or {}), **payload, "recovered": "窗口内不再越限" if enabled else "规则已禁用"}
            log.info("告警恢复 asset=%s rule=%s latest=%s", asset.id, rule_key, latest)
            stats["recovered"] += 1
        return
    message = (
        f"{asset.hostname or asset.id} {label} 当前 {_fmt(latest)}{unit}，"
        f"已持续约 {win} 秒（阈值 {op} {threshold}{unit}）"
        if not detail
        else f"{asset.hostname or asset.id} {detail}"
    )
    if ev is None:
        ev = AnomalyEvent(
            event_id=event_id,
            hostid="",
            host=asset.id,
            hostname=asset.hostname or asset.id,
            ip=ip,
            trigger_name=label,
            severity=level,
            message=message,
            status="abnormal",
            asset_id=asset.id,
            payload={**payload, "repeat_count": 1, "notify_minutes": notify_minutes, "last_notified_at": now.isoformat()},
        )
        db.add(ev)
        db.flush()  # 取自增 id，供自动诊断采集
        stats["triggered"] += 1
        stats.setdefault("diag_ids", []).append(ev.id)
        log.warning(
            "告警触发 asset=%s rule=%s level=%s latest=%s threshold=%s window=%ss msg=%s",
            asset.id, rule_key, level, latest, threshold, win, message,
        )
    elif ev.status != "abnormal":
        ev.status = "abnormal"
        ev.recovered_at = None
        ev.last_seen_at = now
        ev.severity = level
        ev.message = message
        ev.payload = {**payload, "repeat_count": 1, "notify_minutes": notify_minutes, "last_notified_at": now.isoformat()}
        db.flush()
        stats["triggered"] += 1
        stats.setdefault("diag_ids", []).append(ev.id)
        log.warning(
            "告警再次触发 asset=%s rule=%s level=%s latest=%s threshold=%s window=%ss msg=%s",
            asset.id, rule_key, level, latest, threshold, win, message,
        )
    else:
        ev.last_seen_at = now
        if ev.severity != level:
            ev.severity = level
        merged = {**(ev.payload or {}), "latest": latest, "samples": samples, "level": level, "notify_minutes": notify_minutes}
        # 持续 abnormal 的重复提醒：每满 notify_minutes 分钟重新提醒一次
        repeat = int(merged.get("repeat_count") or 1)
        last_notified = merged.get("last_notified_at")
        if notify_minutes > 0:
            base = last_notified or ev.first_seen_at.isoformat()
            try:
                base_dt = datetime.fromisoformat(str(base))
                due = (now - base_dt).total_seconds() >= notify_minutes * 60
            except (TypeError, ValueError):
                due = False
            if due:
                repeat += 1
                merged["repeat_count"] = repeat
                merged["last_notified_at"] = now.isoformat()
                suffix = f"（第 {repeat} 次提醒，间隔 {notify_minutes} 分钟）"
                if suffix not in ev.message:
                    ev.message = f"{ev.message.split('（第')[0]}{suffix}"
                stats["re_alerted"] = stats.get("re_alerted", 0) + 1
        ev.payload = merged


def run_alert_cycle(db: Session) -> dict:
    """扫描全部子机：按生效策略逐规则判定，产出/恢复异常事件。返回统计。

    扫描范围：child 资产 + 已接入 agent（存在上报样本）的 mother 资产——
    母机同样可安装 agent 并套用告警策略。
    """
    now = utcnow()
    stats = {"checked": 0, "triggered": 0, "recovered": 0, "re_alerted": 0}
    reported_ids = set(
        db.execute(select(SystemMetricSample.asset_id).distinct()).scalars().all()
    )
    children = [
        a
        for a in db.scalars(select(Asset)).all()
        if a.kind == "child" or a.id in reported_ids
    ]
    for asset in children:
        stats["checked"] += 1
        policy, source = effective_policy(db, asset)
        rules = policy.get("rules") or {}
        enabled_rules = {rid: c for rid, c in rules.items() if c.get("enabled")}
        windows = [policy[f"{n}_window_seconds"] for n in ("cpu", "mem", "load")]
        windows += [policy[f"{n}_window_seconds"] for n in ("swap", "inode", "disk_io", "net_perf", "bandwidth", "tcp_conn", "process", "port")]
        windows += [c["window_seconds"] for c in enabled_rules.values()]
        max_win = max(windows) if windows else 300
        rows = db.scalars(
            select(SystemMetricSample)
            .where(
                SystemMetricSample.asset_id == asset.id,
                SystemMetricSample.ts >= now - timedelta(seconds=max_win),
            )
            .order_by(SystemMetricSample.ts.desc())
            .limit(_MAX_ROWS)
        ).all()
        rows.reverse()  # 恢复时间正序
        provision = (asset.extra or {}).get("provision") or {}
        ip = str(provision.get("ip") or "")
        common = {"policy_source": source}

        def series(field: str, win: int, *, ext_key: bool = False, numeric: bool = True) -> list:
            """窗口内样本序列（时间正序）。ext_key=True 时从样本 ext 列取值。"""
            start = now - timedelta(seconds=win)
            out = []
            for r in rows:
                if r.ts < start:
                    continue
                v = (r.ext or {}).get(field) if ext_key else getattr(r, field)
                if numeric:
                    if isinstance(v, (int, float)):
                        out.append(float(v))
                elif v is not None:
                    out.append(v)
            return out

        def series_pts(field: str, win: int, *, ext_key: bool = False, numeric: bool = True) -> list:
            """窗口内样本序列（带时间戳，告警 payload 上下文用）：[(iso_ts, value)]。"""
            start = now - timedelta(seconds=win)
            out = []
            for r in rows:
                if r.ts < start:
                    continue
                v = (r.ext or {}).get(field) if ext_key else getattr(r, field)
                if numeric:
                    if isinstance(v, (int, float)):
                        out.append((r.ts.isoformat(timespec="seconds"), round(float(v), 3)))
                elif v is not None:
                    out.append((r.ts.isoformat(timespec="seconds"), v))
            return out

        # 1) 基础指标（CPU/内存/负载）
        for field, th_key, win_key, level_key, label, unit in BASE_METRICS:
            win = int(policy[win_key])
            values = series(field, win)
            breach = len(values) >= 2 and all(v >= policy[th_key] for v in values)
            _upsert_event(
                db, stats, now, asset, ip,
                rule_key=field, label=label, unit=unit, level=policy[level_key],
                enabled=True, breach=breach, latest=values[-1] if values else None,
                win=win, threshold=policy[th_key], op="gte",
                samples=len(values), metric=field, **common,
                notify_minutes=int(policy.get(f"{field}_notify_minutes") or 0),
                window_series=series_pts(field, win),
            )

        # 2) 系统扩展指标（swap/inode/IO/网络/带宽/TCP）
        for ext_key, en_key, th_key, win_key, level_key, label, unit in EXT_METRICS:
            win = int(policy[win_key])
            values = series(ext_key, win, ext_key=True)
            breach = len(values) >= 2 and all(v >= policy[th_key] for v in values)
            notify_key = en_key.replace("_enabled", "_notify_minutes")
            _upsert_event(
                db, stats, now, asset, ip,
                rule_key=ext_key, label=label, unit=unit, level=policy[level_key],
                enabled=bool(policy[en_key]), breach=breach, latest=values[-1] if values else None,
                win=win, threshold=policy[th_key], op="gte",
                samples=len(values), metric=ext_key, **common,
                notify_minutes=int(policy.get(notify_key) or 0),
                window_series=series_pts(ext_key, win, ext_key=True),
            )

        # 3) OOM kill（事件型：窗口内出现过 oom_events>0 即异常；瞬时事件默认不重复提醒）
        # 上下文：agent 上报的 oom_detail（被杀进程 pid/名称/内存占用/kmsg 时刻），
        # 告警消息直接点名受害进程，payload 保留全部明细供详情页回溯。
        win = int(policy["oom_window_seconds"])
        oom_vals = series("oom_events", win, ext_key=True)
        oom_breach = bool(oom_vals) and any(v > 0 for v in oom_vals)
        oom_count = int(sum(v for v in oom_vals if v > 0)) if oom_vals else 0
        oom_details: list[str] = []
        for item in series("oom_detail", win, ext_key=True, numeric=False):
            if isinstance(item, list):
                oom_details.extend(str(x) for x in item)
        oom_details = list(dict.fromkeys(oom_details))[-8:]  # 去重保序，最多留 8 条
        detail = f"检测到 OOM kill（窗口 {win} 秒内 {oom_count} 次）"
        if oom_details:
            detail = f"检测到 OOM kill：{oom_details[-1]}（窗口 {win} 秒内 {oom_count} 次）"
        _upsert_event(
            db, stats, now, asset, ip,
            rule_key="oom", label="OOM kill 事件", unit="次", level=policy["oom_level"],
            enabled=bool(policy["oom_enabled"]), breach=oom_breach,
            latest=oom_count or None, win=win, threshold=1, op="gte",
            samples=len(oom_vals), detail=detail,
            metric="oom_events", **common,
            notify_minutes=int(policy.get("oom_notify_minutes") or 0),
            window_series=series_pts("oom_events", win, ext_key=True),
            extra={"oom_detail": oom_details},
        )

        # 4) 关键进程消失
        win = int(policy["process_window_seconds"])
        proc_series = series("procs_missing", win, ext_key=True, numeric=False)
        missing: list[str] = []
        for item in proc_series:
            if isinstance(item, list):
                missing.extend(str(x) for x in item)
        missing = sorted(set(missing))
        _upsert_event(
            db, stats, now, asset, ip,
            rule_key="process", label="关键进程消失", unit="", level=policy["process_level"],
            enabled=bool(policy["process_enabled"]), breach=bool(missing),
            latest=", ".join(missing) or None, win=win,
            detail=f"关键进程异常退出：{', '.join(missing)}" if missing else "",
            samples=len(proc_series), metric="procs_missing", **common,
            notify_minutes=int(policy.get("process_notify_minutes") or 0),
        )

        # 5) 关键端口探活失败
        win = int(policy["port_window_seconds"])
        port_series = series("ports_down", win, ext_key=True, numeric=False)
        down_ports: list[str] = []
        for item in port_series:
            if isinstance(item, list):
                down_ports.extend(str(x) for x in item)
        down_ports = sorted(set(down_ports), key=lambda x: (len(x), x))
        _upsert_event(
            db, stats, now, asset, ip,
            rule_key="port", label="关键端口探活失败", unit="", level=policy["port_level"],
            enabled=bool(policy["port_enabled"]), breach=bool(down_ports),
            latest=", ".join(down_ports) or None, win=win,
            detail=f"端口探活失败：{', '.join(down_ports)}" if down_ports else "",
            samples=len(port_series), metric="ports_down", **common,
            notify_minutes=int(policy.get("port_notify_minutes") or 0),
            window_series=series_pts("ports_down", win, ext_key=True, numeric=False),
        )

        # 6) 应用/数据库层规则目录（数据源：agent metrics 抓取，ext.metrics）
        # 全量遍历：disabled 规则同样进入判定以便自动恢复既有 abnormal 事件
        for rid, cfg in rules.items():
            meta = RULE_CATALOG_BY_ID.get(rid)
            if meta is None:
                continue
            enabled = bool(cfg.get("enabled"))
            win = int(cfg["window_seconds"])
            values: list[float] = []
            pts: list[tuple[str, float]] = []
            if enabled:
                start = now - timedelta(seconds=win)
                for r in rows:
                    if r.ts < start:
                        continue
                    m = (r.ext or {}).get("metrics") or {}
                    v = m.get(meta["metric"])
                    if isinstance(v, (int, float)):
                        values.append(float(v))
                        pts.append((r.ts.isoformat(timespec="seconds"), round(float(v), 4)))
            breach = enabled and len(values) >= 2 and all(_breach(meta["op"], v, cfg["threshold"]) for v in values)
            _upsert_event(
                db, stats, now, asset, ip,
                rule_key=rid, label=meta["name"], unit=meta["unit"], level=cfg["level"],
                enabled=enabled, breach=breach, latest=values[-1] if values else None,
                win=win, threshold=cfg["threshold"], op=meta["op"],
                samples=len(values), metric=meta["metric"], **common,
                notify_minutes=int(cfg.get("notify_minutes", meta.get("notify_minutes", 0)) or 0),
                window_series=pts,
            )
    db.commit()
    # 首次触发/再次触发的 agent 告警：提交后自动 SSH 采集异常时刻进程快照
    # （独立 Session，与 webhook 链路同一诊断入口；默认后台线程，不阻塞扫描）
    diag_ids = stats.get("diag_ids") or []
    if diag_ids:
        from app.config import get_settings
        from app.services import diagnostics as diagnostics_svc

        if get_settings().diagnostics_async:
            for aid in diag_ids:
                threading.Thread(target=diagnostics_svc.collect_for_anomaly, args=(aid,), daemon=True).start()
        else:
            for aid in diag_ids:
                diagnostics_svc.collect_for_anomaly(aid)
        stats["diag_snapshot"] = len(diag_ids)
    return stats
