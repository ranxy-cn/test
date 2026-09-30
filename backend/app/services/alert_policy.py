"""告警策略 v2：P0-P3 级别体系 + 秒制窗口 + 系统资源/进程端口/应用/数据库规则目录。

级别定义（全站统一颜色：P0 红 / P1 橙 / P2 黄 / P3 蓝灰）：
- P0 严重故障：服务不可用、核心业务中断、数据库主从中断、磁盘满、OOM、证书 1 天内过期
- P1 重要告警：CPU/内存持续高位运行等
- P2 一般告警：磁盘 80%、证书 30 天、慢查询、日志 ERROR 等
- P3 提示信息：容量预测、成本优化建议、系统巡检通知

策略优先级：子机自有 extra["alert_policy"] > 所属母机 extra["alert_policy"] > 平台默认。
策略结构 v2：
- 基础指标（CPU/内存/负载）：{x}_threshold + {x}_window_seconds（秒制）+ {x}_level
- 系统资源/进程端口：{key}_enabled 开关 + 阈值 + 窗口（秒）+ level，扁平 key
- 应用/数据库层：rules = {rule_id: {enabled, threshold, window_seconds, level}}，
  规则目录见 RULE_CATALOG（指标名/判定符固定，阈值/窗口/级别/开关可配）；
  数据源由 agent 的 metrics_scrape（Prometheus 文本抓取）提供，未启用不触发。
兼容 v1：window_minutes（分钟）读取时自动 ×60 转换为 window_seconds。
时间单位统一为秒，策略变更后 agent 下轮 config_refresh 拉取即生效（无需重启）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Asset

ALERT_LEVELS = ("P0", "P1", "P2", "P3")

LEVEL_LABELS = {"P0": "严重故障", "P1": "重要告警", "P2": "一般告警", "P3": "提示信息"}

# 全站统一级别颜色（告警面板/通知消息一致视觉区分）
LEVEL_COLORS = {"P0": "#ff3b30", "P1": "#ff9500", "P2": "#f7ba2a", "P3": "#909399"}

MAX_WINDOW_SECONDS = 86400  # 窗口上限 24h
MIN_WINDOW_SECONDS = 10

# ---------- 基础指标（秒制，级别默认按规范：CPU/内存持续高=P1） ----------

DEFAULT_POLICY: dict = {
    "cpu_threshold": 90,
    "cpu_window_seconds": 300,
    "cpu_level": "P1",
    "mem_threshold": 90,
    "mem_window_seconds": 300,
    "mem_level": "P1",
    "load_threshold": 1.5,
    "load_window_seconds": 300,
    "load_level": "P1",
    # OOM kill（事件型：发生即告警，窗口内无新事件自动恢复）
    "oom_enabled": False,
    "oom_window_seconds": 300,
    "oom_level": "P1",
    # Swap 使用率
    "swap_enabled": False,
    "swap_threshold": 80,
    "swap_window_seconds": 300,
    "swap_level": "P2",
    # Inode 使用率
    "inode_enabled": False,
    "inode_threshold": 80,
    "inode_window_seconds": 300,
    "inode_level": "P2",
    # 磁盘 IO 延迟（await，毫秒）
    "disk_io_enabled": False,
    "disk_io_threshold": 100,
    "disk_io_window_seconds": 300,
    "disk_io_level": "P2",
    # 网络丢包率(%) / 延迟(ms)
    "net_perf_enabled": False,
    "net_loss_threshold": 1.0,
    "net_latency_threshold": 100,
    "net_perf_window_seconds": 300,
    "net_perf_level": "P2",
    "net_probe_target": "223.5.5.5:443",  # agent 网络探测目标（host:port，TCP 连接计时）
    # 带宽使用率(%)：出口/入口
    "bandwidth_enabled": False,
    "bandwidth_threshold": 80,
    "bandwidth_window_seconds": 600,
    "bandwidth_level": "P2",
    # TCP 连接：TIME_WAIT 数量 / 总连接接近上限(%)
    "tcp_conn_enabled": False,
    "tcp_time_wait_threshold": 10000,
    "tcp_conn_pct_threshold": 80,
    "tcp_conn_window_seconds": 300,
    "tcp_conn_level": "P2",
    # 关键进程消失（nginx/mysql/java 等）
    "process_enabled": False,
    "process_items": ["nginx", "mysqld", "java"],
    "process_window_seconds": 60,
    "process_level": "P0",
    # 关键端口探活失败
    "port_enabled": False,
    "port_items": [22, 80, 443],
    "port_window_seconds": 60,
    "port_level": "P0",
    # 应用/数据库指标抓取数据源（Prometheus 文本 URL 列表，agent 抓取上报）
    "metrics_scrape_enabled": False,
    "metrics_urls": [],
}

# 布尔开关 / 列表 字段（normalize 用）
_BOOL_KEYS = tuple(k for k in DEFAULT_POLICY if k.endswith("_enabled"))
_LIST_KEYS = ("process_items", "port_items", "metrics_urls")

# ---------- 应用 / 数据库层规则目录（阈值/窗口/级别/开关全部可配） ----------
# op: gte/ge/gt/lt/lte；metric 为 agent metrics 抓取上报的指标名（Prometheus 指标或内置约定名）

RULE_CATALOG: list[dict] = [
    # ===== 应用层（15 项，5xx 含紧急档共 16 条） =====
    {"id": "app_health", "group": "app", "name": "服务不可用（健康检查连续失败）", "unit": "次",
     "metric": "app_health_fail_streak", "op": "gte", "threshold": 3, "window_seconds": 60, "level": "P0"},
    {"id": "app_http_5xx", "group": "app", "name": "HTTP 5xx 错误率", "unit": "%",
     "metric": "app_http_5xx_rate", "op": "gt", "threshold": 1.0, "window_seconds": 120, "level": "P1"},
    {"id": "app_http_5xx_critical", "group": "app", "name": "HTTP 5xx 错误率（紧急档）", "unit": "%",
     "metric": "app_http_5xx_rate", "op": "gte", "threshold": 5.0, "window_seconds": 60, "level": "P0"},
    {"id": "app_http_4xx_surge", "group": "app", "name": "HTTP 4xx 激增（同比增长率）", "unit": "%",
     "metric": "app_http_4xx_growth_pct", "op": "gte", "threshold": 300, "window_seconds": 300, "level": "P2"},
    {"id": "app_latency_p95", "group": "app", "name": "接口响应时间 P95", "unit": "ms",
     "metric": "app_latency_p95_ms", "op": "gt", "threshold": 500, "window_seconds": 300, "level": "P2"},
    {"id": "app_latency_p99", "group": "app", "name": "接口响应时间 P99", "unit": "ms",
     "metric": "app_latency_p99_ms", "op": "gt", "threshold": 1000, "window_seconds": 300, "level": "P2"},
    {"id": "app_qps_drop", "group": "app", "name": "QPS 骤降（同比昨日下降）", "unit": "%",
     "metric": "app_qps_drop_pct", "op": "gte", "threshold": 50, "window_seconds": 60, "level": "P1"},
    {"id": "app_qps_overload", "group": "app", "name": "QPS 骤增（容量上限占比）", "unit": "%",
     "metric": "app_qps_capacity_pct", "op": "gte", "threshold": 80, "window_seconds": 60, "level": "P2"},
    {"id": "app_timeout", "group": "app", "name": "请求超时率", "unit": "%",
     "metric": "app_timeout_rate", "op": "gt", "threshold": 1.0, "window_seconds": 300, "level": "P1"},
    {"id": "app_thread_pool", "group": "app", "name": "线程池使用率", "unit": "%",
     "metric": "app_thread_pool_used_pct", "op": "gte", "threshold": 90, "window_seconds": 60, "level": "P1"},
    {"id": "app_conn_pool_wait", "group": "app", "name": "连接池等待数", "unit": "个",
     "metric": "app_conn_pool_waiting", "op": "gt", "threshold": 0, "window_seconds": 120, "level": "P1"},
    {"id": "app_queue_backlog", "group": "app", "name": "队列积压（持续增长）", "unit": "条",
     "metric": "app_queue_depth_growth", "op": "gt", "threshold": 0, "window_seconds": 300, "level": "P1"},
    {"id": "app_jvm_full_gc", "group": "app", "name": "JVM Full GC 频率", "unit": "次/分",
     "metric": "app_jvm_full_gc_per_min", "op": "gte", "threshold": 2, "window_seconds": 300, "level": "P2"},
    {"id": "app_jvm_gc_time", "group": "app", "name": "JVM GC 时间占比", "unit": "%",
     "metric": "app_jvm_gc_time_pct", "op": "gte", "threshold": 20, "window_seconds": 300, "level": "P2"},
    {"id": "app_heap_high", "group": "app", "name": "JVM 堆内存使用率", "unit": "%",
     "metric": "app_heap_used_pct", "op": "gte", "threshold": 85, "window_seconds": 300, "level": "P2"},
    {"id": "app_log_error_surge", "group": "app", "name": "日志 ERROR 突增（倍数）", "unit": "倍",
     "metric": "app_log_error_growth_ratio", "op": "gte", "threshold": 5, "window_seconds": 60, "level": "P2"},
    {"id": "app_deploy_anomaly", "group": "app", "name": "发布后异常（5xx/延迟上升）", "unit": "%",
     "metric": "app_deploy_error_rate", "op": "gt", "threshold": 1.0, "window_seconds": 300, "level": "P0"},
    {"id": "app_downstream_fail", "group": "app", "name": "依赖下游失败率", "unit": "%",
     "metric": "app_downstream_fail_rate", "op": "gt", "threshold": 5.0, "window_seconds": 120, "level": "P1"},
    # ===== MySQL（6 项） =====
    {"id": "mysql_conn_pct", "group": "db", "name": "MySQL 连接数接近上限", "unit": "%",
     "metric": "mysql_threads_connected_pct", "op": "gte", "threshold": 80, "window_seconds": 60, "level": "P1"},
    {"id": "mysql_slow_query_surge", "group": "db", "name": "MySQL 慢查询突增", "unit": "%",
     "metric": "mysql_slow_query_growth_pct", "op": "gte", "threshold": 100, "window_seconds": 300, "level": "P2"},
    {"id": "mysql_replica_lag", "group": "db", "name": "MySQL 主从延迟", "unit": "s",
     "metric": "mysql_replica_lag_seconds", "op": "gt", "threshold": 30, "window_seconds": 60, "level": "P1"},
    {"id": "mysql_replica_broken", "group": "db", "name": "MySQL 复制中断", "unit": "0/1",
     "metric": "mysql_replica_running", "op": "lte", "threshold": 0, "window_seconds": 60, "level": "P0"},
    {"id": "mysql_lock_waits", "group": "db", "name": "MySQL 锁等待/死锁过多", "unit": "次/分",
     "metric": "mysql_lock_deadlock_per_min", "op": "gte", "threshold": 10, "window_seconds": 300, "level": "P1"},
    {"id": "mysql_disk_free", "group": "db", "name": "MySQL 磁盘空间不足", "unit": "%",
     "metric": "mysql_disk_free_pct", "op": "lt", "threshold": 10, "window_seconds": 60, "level": "P0"},
    # ===== Redis（4 项） =====
    {"id": "redis_mem_pct", "group": "db", "name": "Redis 内存使用率", "unit": "%",
     "metric": "redis_mem_used_pct", "op": "gte", "threshold": 85, "window_seconds": 60, "level": "P1"},
    {"id": "redis_hit_rate_drop", "group": "db", "name": "Redis 命中率骤降（降幅）", "unit": "%",
     "metric": "redis_hit_rate_drop_pct", "op": "gte", "threshold": 20, "window_seconds": 300, "level": "P2"},
    {"id": "redis_replica_broken", "group": "db", "name": "Redis 主从切换/复制中断", "unit": "0/1",
     "metric": "redis_replication_ok", "op": "lte", "threshold": 0, "window_seconds": 60, "level": "P1"},
    {"id": "redis_persist_fail", "group": "db", "name": "Redis 持久化失败", "unit": "0/1",
     "metric": "redis_persist_ok", "op": "lte", "threshold": 0, "window_seconds": 60, "level": "P1"},
    # ===== 消息队列 MQ（4 项） =====
    {"id": "mq_backlog", "group": "db", "name": "MQ 消息堆积", "unit": "条",
     "metric": "mq_lag_count", "op": "gte", "threshold": 10000, "window_seconds": 300, "level": "P1"},
    {"id": "mq_consume_delay", "group": "db", "name": "MQ 消费延迟", "unit": "s",
     "metric": "mq_consume_delay_seconds", "op": "gt", "threshold": 60, "window_seconds": 300, "level": "P1"},
    {"id": "mq_no_consumer", "group": "db", "name": "MQ 无消费者", "unit": "个",
     "metric": "mq_consumer_count", "op": "lte", "threshold": 0, "window_seconds": 300, "level": "P1"},
    {"id": "mq_dlq_growth", "group": "db", "name": "MQ 死信队列增长", "unit": "%",
     "metric": "mq_dlq_growth_pct", "op": "gte", "threshold": 10, "window_seconds": 300, "level": "P2"},
    # ===== Nginx/LB（3 项） =====
    {"id": "nginx_5xx_rate", "group": "db", "name": "Nginx 5xx 错误率升高", "unit": "%",
     "metric": "nginx_5xx_rate", "op": "gt", "threshold": 1.0, "window_seconds": 120, "level": "P1"},
    {"id": "nginx_upstream_fail", "group": "db", "name": "Nginx upstream 失败", "unit": "次",
     "metric": "nginx_upstream_fail_count", "op": "gte", "threshold": 5, "window_seconds": 60, "level": "P1"},
    {"id": "nginx_conn_pct", "group": "db", "name": "Nginx 连接数接近上限", "unit": "%",
     "metric": "nginx_conn_used_pct", "op": "gte", "threshold": 80, "window_seconds": 60, "level": "P2"},
]

RULE_CATALOG_BY_ID: dict[str, dict] = {r["id"]: r for r in RULE_CATALOG}


def rule_defaults() -> dict[str, dict]:
    """规则目录的默认可配置项：{rule_id: {enabled, threshold, window_seconds, level}}。"""
    return {
        r["id"]: {
            "enabled": False,
            "threshold": r["threshold"],
            "window_seconds": r["window_seconds"],
            "level": r["level"],
        }
        for r in RULE_CATALOG
    }


def _norm_level(value, name: str) -> str:
    lv = str(value or "").strip().upper()
    if lv not in ALERT_LEVELS:
        raise ValueError(f"{name} 级别必须是 {'/'.join(ALERT_LEVELS)} 之一")
    return lv


def _norm_window(value, name: str) -> int:
    try:
        win = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是整数（秒）") from None
    if not MIN_WINDOW_SECONDS <= win <= MAX_WINDOW_SECONDS:
        raise ValueError(f"{name} 取值范围 {MIN_WINDOW_SECONDS}~{MAX_WINDOW_SECONDS} 秒")
    return win


def _norm_rule(rule_id: str, cfg: dict) -> dict | None:
    meta = RULE_CATALOG_BY_ID.get(rule_id)
    if meta is None or not isinstance(cfg, dict):
        return None
    out = {
        "enabled": bool(cfg.get("enabled", False)),
        "threshold": meta["threshold"],
        "window_seconds": meta["window_seconds"],
        "level": meta["level"],
    }
    try:
        out["threshold"] = round(float(cfg.get("threshold", meta["threshold"])), 3)
    except (TypeError, ValueError):
        raise ValueError(f"规则 {rule_id} 的 threshold 必须是数字") from None
    out["window_seconds"] = _norm_window(cfg.get("window_seconds", meta["window_seconds"]), f"规则 {rule_id} 窗口")
    out["level"] = _norm_level(cfg.get("level", meta["level"]), f"规则 {rule_id}")
    return out


def normalize_policy(data: dict | None) -> dict:
    """校验并规范化 v2 策略；缺失字段补默认值，非法值抛 ValueError。

    兼容 v1（分钟制）：出现 {x}_window_minutes 时自动 ×60 写入 {x}_window_seconds。
    """
    policy = dict(DEFAULT_POLICY)
    policy["rules"] = rule_defaults()
    src = dict(data or {})
    # v1 分钟窗口 → 秒（先转换再覆盖）
    for name in ("cpu", "mem", "load"):
        legacy = src.pop(f"{name}_window_minutes", None)
        if legacy is not None and f"{name}_window_seconds" not in src:
            try:
                src[f"{name}_window_seconds"] = int(legacy) * 60
            except (TypeError, ValueError):
                pass

    for key, value in src.items():
        if key == "rules":
            continue
        if key in policy:
            policy[key] = value

    # 基础指标阈值/窗口/级别
    for name in ("cpu_threshold", "mem_threshold"):
        try:
            policy[name] = int(policy[name])
        except (TypeError, ValueError):
            raise ValueError(f"告警策略字段 {name} 必须是整数") from None
        if not 1 <= policy[name] <= 99:
            raise ValueError(f"{name} 取值范围 1~99")
    try:
        policy["load_threshold"] = round(float(policy["load_threshold"]), 2)
    except (TypeError, ValueError):
        raise ValueError("告警策略字段 load_threshold 必须是数字") from None
    if not 0.1 <= policy["load_threshold"] <= 100:
        raise ValueError("load_threshold 取值范围 0.1~100")
    for name in ("cpu", "mem", "load"):
        policy[f"{name}_window_seconds"] = _norm_window(policy[f"{name}_window_seconds"], f"{name}_window_seconds")
        policy[f"{name}_level"] = _norm_level(policy.get(f"{name}_level"), f"{name}_level")

    # 系统资源 / 进程端口开关项
    for key in _BOOL_KEYS:
        policy[key] = bool(policy[key])
    pct_keys = (
        "swap_threshold", "inode_threshold", "bandwidth_threshold", "tcp_conn_pct_threshold",
    )
    for key in pct_keys:
        try:
            policy[key] = round(float(policy[key]), 2)
        except (TypeError, ValueError):
            raise ValueError(f"告警策略字段 {key} 必须是数字") from None
        if not 1 <= policy[key] <= 100:
            raise ValueError(f"{key} 取值范围 1~100")
    num_keys = ("disk_io_threshold", "net_loss_threshold", "net_latency_threshold", "tcp_time_wait_threshold")
    for key in num_keys:
        try:
            policy[key] = round(float(policy[key]), 2)
        except (TypeError, ValueError):
            raise ValueError(f"告警策略字段 {key} 必须是数字") from None
        if policy[key] <= 0:
            raise ValueError(f"{key} 必须大于 0")
    for name in ("swap", "inode", "disk_io", "net_perf", "bandwidth", "tcp_conn", "process", "port"):
        policy[f"{name}_window_seconds"] = _norm_window(policy[f"{name}_window_seconds"], f"{name}_window_seconds")
        policy[f"{name}_level"] = _norm_level(policy.get(f"{name}_level"), f"{name}_level")

    # 列表项
    items = policy.get("process_items")
    if not isinstance(items, list):
        raise ValueError("process_items 必须是字符串列表")
    policy["process_items"] = [str(x).strip() for x in items if str(x).strip()][:32]
    ports = policy.get("port_items")
    if not isinstance(ports, list):
        raise ValueError("port_items 必须是端口列表")
    out_ports: list[int] = []
    for p in ports:
        try:
            pv = int(p)
        except (TypeError, ValueError):
            raise ValueError("port_items 必须是端口列表") from None
        if not 1 <= pv <= 65535:
            raise ValueError("port_items 端口取值范围 1~65535")
        out_ports.append(pv)
    policy["port_items"] = out_ports[:64]
    urls = policy.get("metrics_urls")
    if not isinstance(urls, list):
        raise ValueError("metrics_urls 必须是 URL 列表")
    clean_urls = []
    for u in urls:
        u = str(u).strip()
        if u.lower().startswith(("http://", "https://")):
            clean_urls.append(u)
    policy["metrics_urls"] = clean_urls[:16]
    policy["net_probe_target"] = str(policy.get("net_probe_target") or "").strip()[:128]

    # 应用/数据库层规则
    rules_in = src.get("rules")
    if rules_in is not None and not isinstance(rules_in, dict):
        raise ValueError("rules 必须是对象")
    rules = rule_defaults()
    for rid, cfg in (rules_in or {}).items():
        normed = _norm_rule(rid, cfg or {})
        if normed is not None:
            rules[rid] = normed
    policy["rules"] = rules
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
    return normalize_policy(None), "default"


# ---------- 配置模板（快速应用标准配置） ----------

_POLICY_ALL_ON = {
    **{k: True for k in _BOOL_KEYS},
    "metrics_scrape_enabled": True,
    "rules": {rid: {"enabled": True} for rid in RULE_CATALOG_BY_ID},
}

POLICY_TEMPLATES: dict[str, dict] = {
    "standard": {
        "label": "标准模板（P0/P1 关键项）",
        "policy": {
            "oom_enabled": True,
            "process_enabled": True,
            "port_enabled": True,
            "rules": {
                "app_health": {"enabled": True},
                "app_http_5xx_critical": {"enabled": True},
                "mysql_replica_broken": {"enabled": True},
            },
        },
    },
    "strict": {"label": "严格模板（全量开启）", "policy": _POLICY_ALL_ON},
    "relaxed": {
        "label": "宽松模板（仅核心可用性）",
        "policy": {
            "oom_enabled": True,
            "process_enabled": True,
            "process_level": "P0",
            "port_enabled": True,
            "port_level": "P0",
        },
    },
}


def normalize_template_policy(data: dict | None) -> dict:
    """模板策略同样走 normalize（保证结构与直接保存一致）。"""
    return normalize_policy(data)
