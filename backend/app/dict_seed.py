"""业务字典种子：告警标题 / 资产 / 预案 的中文枚举映射。

与 menus_seed 同一模式：只补缺失项（按 dict_type+code 判断），
不覆盖已有数据，管理员后续可在字典里自行维护。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DictEntry

# (dict_type, code, label, sort_order, remark)
DICT_SEED: list[tuple[str, str, str, int, str]] = [
    # ===== 告警标题（trigger）=====
    ("trigger", "CPU usage > 85% for 5 minutes", "CPU 使用率持续超 85%（5 分钟）", 10, "Zabbix 触发器"),
    ("trigger", "CPU usage too high", "CPU 使用率过高", 20, "Zabbix 触发器"),
    ("trigger", "MySQL replication lag too high", "MySQL 主从复制延迟过高", 30, "Zabbix 触发器"),
    ("trigger", "mystery native crash", "未知原因进程崩溃", 40, "演练用未知故障"),
    # ===== 资产（asset）=====
    ("asset", "ast-order-app-01", "订单系统-应用01", 10, ""),
    ("asset", "ast-order-app-02", "订单系统-应用02", 20, ""),
    ("asset", "ast-order-app-03", "订单系统-应用03", 30, ""),
    ("asset", "ast-order-db-01", "订单系统-数据库01", 40, ""),
    ("asset", "ast-order-gw-01", "订单系统-网关01", 50, ""),
    ("asset", "ast-order-job-01", "订单系统-作业01", 60, ""),
    ("asset", "ast-order-lb-01", "订单系统-负载均衡01", 70, ""),
    ("asset", "ast-order-redis-01", "订单系统-缓存01", 80, ""),
    ("asset", "ast-order-unreachable", "订单系统-失联节点", 90, "演示：网络不可达"),
    ("asset", "ast-zabbix-server", "Zabbix 监控服务器", 100, ""),
    # ===== 预案（action）=====
    ("action", "ACT-ROLLING-RESTART", "滚动重启服务", 10, "绿灯预案：分批重启并探活"),
    ("action", "ACT-DB-FAILOVER", "数据库主从切换", 20, "黄灯预案：需人工审批"),
    ("action", "ACT-CLEAN-TMPLOG", "清理临时日志", 30, "磁盘清理预案"),
    ("action", "ACT-RESTART-PROBE", "重启服务并探活", 40, "基础重启预案"),
]


def seed_dict(db: Session) -> None:
    """补缺失的字典项；已存在（含管理员改过的）不覆盖。"""
    existing = {
        (d.dict_type, d.code) for d in db.scalars(select(DictEntry)).all()
    }
    added = False
    for dict_type, code, label, sort_order, remark in DICT_SEED:
        if (dict_type, code) in existing:
            continue
        db.add(
            DictEntry(
                dict_type=dict_type,
                code=code,
                label=label,
                sort_order=sort_order,
                remark=remark,
            )
        )
        added = True
    if added:
        db.flush()
