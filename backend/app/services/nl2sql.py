"""智能数据问答（NL2SQL）：自然语言 → 只读 SQL 查询 → AI 解读。

安全模型（四层防护，缺一不可）：
1. 表白名单：仅业务数据表可查；用户/角色/权限/菜单/登录日志/AI 密钥(app_settings)等
   非业务表一律拒绝；系统库（information_schema/mysql/performance_schema/sys）拒绝。
2. 单库限定：SQL 中出现库名前缀时必须等于本系统库名；跨库（其他业务库）直接拦截。
3. 只读校验：sqlglot 解析 AST 后仅放行 SELECT（含 UNION/子查询/CTE），显式拒绝
   INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE 等写语法、INTO OUTFILE、FOR UPDATE、
   多语句、SLEEP/BENCHMARK/LOAD_FILE 等危险函数；MySQL 执行层再用
   START TRANSACTION READ ONLY 只读事务兜底（会话结束自动还原，不侵入数据库结构）。
4. 资源保护：无 LIMIT 自动补、LIMIT 收敛上限、无过滤条件的裸浏览强制 LIMIT 200、
   SELECT 级 MAX_EXECUTION_TIME 超时 hint，防全表扫描拖垮生产库。

不修改任何现有表结构，不新增触发器/存储过程；查询留痕走应用日志。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import sqlglot
from sqlglot import exp
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.services.knowledge import chat_completion

log = logging.getLogger("nl2sql")

# ---------------------------------------------------------------------------
# 业务表白名单（表名 → 中文说明）。凡不在此集合内的表一律拒绝；
# users/roles/permissions/user_roles/role_permissions/menus/role_menus/
# user_tokens/login_logs/app_settings（存 AI API Key）等非业务表被明确排除。
# ---------------------------------------------------------------------------
ALLOWED_TABLES: dict[str, str] = {
    "assets": "资产台账（母机/子机、IP、负责人、可达性、Agent 状态）",
    "owner_contacts": "负责人通知联系人（姓名与推送渠道配置）",
    "digital_employees": "数字员工档案",
    "maintenance_windows": "维护窗口",
    "tickets": "运维任务单（状态/级别/立案与恢复时间）",
    "ticket_events": "任务单事件流（采集/诊断/执行/审批动作）",
    "approvals": "工单审批记录",
    "audit_logs": "业务操作审计（工单/资产等操作留痕）",
    "alert_events": "告警事件（原始告警流入）",
    "anomaly_events": "异常事件（状态/级别/触发器/首末次时间）",
    "anomaly_logs": "异常相关日志原文",
    "metric_samples": "指标采样（资产指标时序，5 分钟槽）",
    "system_metric_samples": "母机本机系统指标时序",
    "resource_locks": "资源锁（单资产互斥）",
    "action_failures": "动作失败记录（熔断依据）",
    "notifications": "通知留痕（站内信/日志/webhook/负责人推送）",
    "backup_jobs": "备份任务",
    "backup_runs": "备份执行记录",
    "knowledge_documents": "知识库文档（标题/摘要/标签/状态）",
    "ai_analyses": "AI 日志分析结果（结论/诊断/建议/置信度）",
    "ai_audit_logs": "AI 调用审计（分析请求留痕，不含密钥）",
    "recovery_scripts": "恢复预案脚本目录",
    "recovery_tasks": "恢复任务执行记录",
    "dict_entries": "业务字典（触发器/资产/动作中文名）",
}

# 明确拒绝清单（用于拦截原因提示；不在白名单本身就会拒绝，这里保证提示语义明确）
DENIED_TABLES = {
    "users", "roles", "permissions", "user_roles", "role_permissions",
    "menus", "role_menus", "user_tokens", "login_logs", "app_settings",
    "alembic_version",
}

# 禁止跨库访问的系统库
DENIED_DATABASES = {"information_schema", "mysql", "performance_schema", "sys"}

# 危险函数（拖库/DoS/报错注入）
DENIED_FUNCTIONS = {
    "SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK",
    "IS_FREE_LOCK", "IS_USED_LOCK", "EXTRACTVALUE", "UPDATEXML",
}

MAX_LIMIT_BROWSE = 200   # 无 WHERE 且无聚合的裸浏览上限
MAX_LIMIT_QUERY = 1000   # 带过滤或聚合的常规查询上限
MAX_EXECUTION_MS = 8000  # SELECT 级超时（MySQL 8 hint）
MAX_ROWS_RETURN = 50     # 返回给对话/前端的行数截断
MAX_ROWS_TO_LLM = 30     # 送 LLM 解读的行数截断

# ---------------------------------------------------------------------------
# SQL 安全校验与守护
# ---------------------------------------------------------------------------

def current_database() -> str:
    """本系统数据库名（从 DATABASE_URL 解析）。"""
    url = get_settings().database_url
    if url.startswith("sqlite"):
        return "devops_agent"  # sqlite 测试/演示库无库名概念，按默认名做同库校验
    m = re.search(r"/([^/?]+)(?:\?|$)", url)
    return m.group(1) if m else "devops_agent"


def _extract_sql_text(raw: str) -> str:
    """从 LLM 回答中提取 SQL：剥 markdown 代码块与多余说明，截到第一条分号。"""
    text = (raw or "").strip()
    m = re.search(r"```(?:sql)?\s*(.+?)\s*```", text, re.S | re.I)
    if m:
        text = m.group(1).strip()
    # 去掉前置说明：定位第一条 SELECT/WITH
    m = re.search(r"(?is)\b(select|with)\b.*", text)
    if m:
        text = m.group(0).strip()
    # 只保留第一条语句
    text = text.split(";", 1)[0].strip()
    return text


def _table_ref_ok(table: exp.Table) -> str | None:
    """校验单个表引用：白名单 + 禁止清单 + 同库限定。返回拒绝原因或 None。"""
    name = (table.name or "").lower()
    db_part = (table.db or "").lower()
    catalog = (table.catalog or "").lower()
    if db_part and db_part != current_database().lower():
        return f"禁止跨库访问：{db_part}.{name}（仅限本系统库 {current_database()}）"
    if catalog:
        return f"禁止访问：{catalog}.{db_part}.{name}（多段库名限定）"
    if db_part in DENIED_DATABASES or name in DENIED_DATABASES:
        return f"禁止访问系统库：{name}"
    if name in DENIED_TABLES:
        return f"禁止访问非业务表：{name}（用户/权限/密钥等敏感表不在开放范围）"
    if name not in ALLOWED_TABLES:
        return f"表不在业务白名单：{name}"
    return None


def _func_ok(func: exp.Expression) -> str | None:
    """校验函数调用是否在危险函数清单。"""
    if isinstance(func, exp.Anonymous):
        name = str(func.this or "").upper()
    else:
        name = func.__class__.__name__.upper()
    return f"禁止危险函数：{name}" if name in DENIED_FUNCTIONS else None


def _limit_of(node: exp.Expression) -> exp.Limit | None:
    return node.args.get("limit") if hasattr(node, "args") else None


def _literal_limit(node: exp.Expression) -> int | None:
    limit = _limit_of(node)
    if limit is None:
        return None
    this = limit.args.get("expression")
    if isinstance(this, exp.Literal) and this.is_int:
        return int(this.name)
    return None  # 非字面量 LIMIT（表达式/占位符）按不支持处理


def _set_limit(node: exp.Expression, value: int) -> exp.Expression:
    return node.limit(value)  # sqlglot limit() 返回副本，调用方需重新赋值


def validate_and_guard(raw_sql: str) -> tuple[str, str | None]:
    """SQL 安全审计。返回 (守护后的 SQL, 拒绝原因)；原因非 None 表示已拦截。

    校验项：单条语句、仅 SELECT/UNION、表白名单、同库限定、危险函数、
    INTO OUTFILE/DUMPFILE、FOR UPDATE/SHARE、无参数占位符、LIMIT 收敛。
    """
    text = (raw_sql or "").strip().rstrip(";").strip()
    if not text:
        return "", "SQL 为空"

    if "?" in text or re.search(r":\w+", text):
        return "", "禁止参数占位符，仅支持已内联的完整语句"

    try:
        statements = sqlglot.parse(text, read="mysql")
    except Exception as e:  # 解析失败一律拒绝（防注释/编码绕过）
        return "", f"SQL 语法无法解析，已拦截：{e}"

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return "", "仅允许单条 SELECT 查询（检测到多语句）"
    node = statements[0]

    # 仅允许 SELECT（含 WITH CTE 的 Select、UNION 组合查询）
    if not isinstance(node, (exp.Select, exp.Union)):
        return "", f"仅允许 SELECT 查询（检测到 {node.__class__.__name__}）"

    # 原生未识别命令（CALL/SET/SHOW 等）一律拒绝
    if node.find(exp.Command):
        return "", "检测到非查询命令，已拦截"

    # 表引用逐一校验（覆盖 JOIN/子查询/CTE/UNION 内所有表；CTE 别名放行，其内部表已校验）
    cte_names = {cte.alias_or_name.lower() for cte in node.find_all(exp.CTE)}
    for table in node.find_all(exp.Table):
        if (table.name or "").lower() in cte_names and not table.db and not table.catalog:
            continue
        reason = _table_ref_ok(table)
        if reason:
            return "", reason

    # 危险函数
    for func in node.find_all(exp.Func):
        reason = _func_ok(func)
        if reason:
            return "", reason

    # INTO OUTFILE/DUMPFILE/@变量
    if node.find(exp.Into):
        return "", "禁止 INTO OUTFILE/DUMPFILE/变量导出"

    # SELECT ... FOR UPDATE / FOR SHARE（只读不允许加锁）
    if node.args.get("locks"):
        return "", "只读模式禁止 FOR UPDATE/FOR SHARE 加锁"

    # LIMIT 守护：无 WHERE 且无聚合的裸浏览收紧到 200，其余上限 1000，缺失自动补
    has_filter = bool(node.find(exp.Where))
    has_agg = bool(node.find(exp.AggFunc))
    cap = MAX_LIMIT_QUERY if (has_filter or has_agg) else MAX_LIMIT_BROWSE
    current = _literal_limit(node)
    if current is None and _limit_of(node) is not None:
        return "", "LIMIT 仅支持数字字面量"
    if current != cap:
        node = _set_limit(node, cap)

    guarded = node.sql(dialect="mysql")
    # SELECT 级超时保护（MySQL 8 optimizer hint；CTE/WITH 开头时跳过，已有 LIMIT 兜底）
    if re.match(r"(?i)^\s*SELECT\b", guarded):
        guarded = re.sub(
            r"(?i)^\s*SELECT\b",
            f"SELECT /*+ MAX_EXECUTION_TIME({MAX_EXECUTION_MS}) */",
            guarded,
            count=1,
        )
    return guarded, None


# ---------------------------------------------------------------------------
# Schema 反射（白名单表的列清单，供 LLM 生成 SQL）
# ---------------------------------------------------------------------------

_schema_cache: dict[str, str] = {}


def build_schema_text(bind: Engine) -> str:
    """反射白名单表的列名清单（带缓存），输出给 LLM。"""
    url_key = str(bind.url)
    cached = _schema_cache.get(url_key)
    if cached:
        return cached
    from sqlalchemy import inspect

    inspector = inspect(bind)
    existing = set(inspector.get_table_names())
    lines: list[str] = []
    for table, desc in ALLOWED_TABLES.items():
        if table not in existing:
            continue
        cols = ", ".join(c["name"] for c in inspector.get_columns(table))
        lines.append(f"{table}（{desc}）: {cols}")
    text = "\n".join(lines)
    _schema_cache[url_key] = text
    return text


# ---------------------------------------------------------------------------
# NL → SQL：LLM 生成 + 无 AI 时关键词 mock 兜底
# ---------------------------------------------------------------------------

def _now_text() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def mock_sql_for(question: str) -> str | None:
    """未配置 AI 网关时的演示兜底：关键词映射预置白名单查询。"""
    q = (question or "").lower()
    if any(k in q for k in ("资产", "主机", "服务器", "多少台")):
        if any(k in q for k in ("可达", "在线")):
            return "SELECT kind, reachable, count(*) AS cnt FROM assets GROUP BY kind, reachable"
        return "SELECT kind, count(*) AS cnt FROM assets GROUP BY kind"
    if "告警" in q or "异常" in q:
        return (
            "SELECT severity, count(*) AS cnt FROM anomaly_events "
            "WHERE status = 'abnormal' GROUP BY severity"
        )
    if "工单" in q or "任务单" in q:
        return "SELECT status, count(*) AS cnt FROM tickets GROUP BY status"
    if "通知" in q:
        return "SELECT kind, count(*) AS cnt FROM notifications GROUP BY kind"
    if "负责人" in q:
        return "SELECT name, channels, email FROM owner_contacts LIMIT 20"
    return None


def generate_sql(question: str, schema_text: str) -> tuple[str, str]:
    """调 LLM 生成 SQL，返回 (sql, model)。失败抛异常由上层降级。"""
    system = (
        "你是 MySQL SQL 生成器。根据表结构与用户问题，只输出一条 SELECT 语句："
        "不要任何解释、不要 markdown、不要分号。"
        f"只能使用给出的表和列；当前数据库为 {current_database()}，禁止写库名前缀；"
        "禁止访问 users/app_settings 等表；禁止任何写操作；"
        f"必须带 LIMIT（不超过 {MAX_LIMIT_QUERY}）；时间条件结合当前时间 {_now_text()}。"
    )
    user = f"表结构：\n{schema_text}\n\n用户问题：{question}"
    raw = chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=600,
    )
    return _extract_sql_text(raw), "llm"


# ---------------------------------------------------------------------------
# 只读执行（MySQL 只读事务兜底；sqlite 测试库直接执行）
# ---------------------------------------------------------------------------

def _execute_mysql_readonly(conn: Any, sql: str) -> tuple[list[str], list[list[Any]]]:
    """MySQL：只读事务执行。事务结束只读限制自动还原，连接归还池后无残留。"""
    conn.exec_driver_sql("START TRANSACTION READ ONLY")
    try:
        result = conn.exec_driver_sql(sql)
        cols = list(result.keys())
        rows = [[v for v in row] for row in result.fetchall()]
    finally:
        conn.exec_driver_sql("ROLLBACK")
    return cols, rows


def execute_readonly(bind: Engine, sql: str) -> tuple[list[str], list[list[Any]]]:
    if bind.dialect.name == "mysql":
        with bind.connect() as conn:
            return _execute_mysql_readonly(conn, sql)
    # sqlite（测试/本地演示）直接执行
    with bind.connect() as conn:
        result = conn.exec_driver_sql(sql)
        cols = list(result.keys())
        rows = [[v for v in row] for row in result.fetchall()]
    return cols, rows


# ---------------------------------------------------------------------------
# 结果解读（LLM 二段生成；异常降级为结构化摘要）
# ---------------------------------------------------------------------------

def _rows_to_text(columns: list[str], rows: list[list[Any]], limit: int) -> str:
    head = " | ".join(columns)
    body = "\n".join(" | ".join("" if v is None else str(v) for v in row) for row in rows[:limit])
    return f"{head}\n{body}"


def interpret_result(question: str, sql: str, columns: list[str], rows: list[list[Any]]) -> tuple[str, str]:
    """LLM 解读查询结果，返回 (answer, model)；未配置/失败时降级模板。"""
    try:
        raw = chat_completion(
            [
                {
                    "role": "system",
                    "content": "你是企业运维数据分析师。基于给出的 SQL 结果用中文解答用户问题，"
                    "给出数据结论与简要分析；不得编造结果里没有的数据；不超过 300 字。",
                },
                {
                    "role": "user",
                    "content": f"用户问题：{question}\n执行的 SQL：{sql}\n"
                    f"结果（前 {min(len(rows), MAX_ROWS_TO_LLM)} 行）：\n"
                    f"{_rows_to_text(columns, rows, MAX_ROWS_TO_LLM)}",
                },
            ],
            max_tokens=700,
        )
        return raw.strip(), "llm"
    except Exception:
        template = (
            f"共查询到 {len(rows)} 行结果（字段：{'、'.join(columns)}）。"
            f"未连接 AI 网关，以下为原始数据：\n{_rows_to_text(columns, rows, 10)}"
        )
        return template, "fallback"


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_data_query(db: Any, question: str, operator: str = "system") -> dict[str, Any]:
    """自然语言数据问答主流程：生成 → 审计 → 只读执行 → 解读。

    校验不通过抛 ValueError（原因在消息里）；执行失败抛 RuntimeError。
    """
    bind = db.get_bind()
    schema_text = build_schema_text(bind)

    model = "mock"
    sql = mock_sql_for(question)
    if sql is None:
        sql, model = generate_sql(question, schema_text)

    guarded, reason = validate_and_guard(sql)
    if reason:
        log.info("nl2sql blocked operator=%s question=%r sql=%r reason=%s", operator, question, sql, reason)
        raise ValueError(reason)

    try:
        columns, rows = execute_readonly(bind, guarded)
    except Exception as e:
        log.info("nl2sql exec failed operator=%s sql=%r err=%s", operator, guarded, e)
        raise RuntimeError(f"查询执行失败：{e}") from e

    answer, interp_model = interpret_result(question, guarded, columns, rows)
    log.info(
        "nl2sql ok operator=%s question=%r sql=%r rows=%s model=%s",
        operator, question, guarded, len(rows), interp_model,
    )
    return {
        "answer": answer,
        "sql": guarded,
        "columns": columns,
        "rows": rows[:MAX_ROWS_RETURN],
        "row_count": len(rows),
        "model": interp_model,
        "sql_source": model,
    }
