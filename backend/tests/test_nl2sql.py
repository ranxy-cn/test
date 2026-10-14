"""NL2SQL 智能数据问答：SQL 安全审计 / 只读执行 / 越权拦截 / API 集成。"""
from __future__ import annotations

import pytest

from app.services import nl2sql
from app.services.nl2sql import (
    MAX_LIMIT_BROWSE,
    MAX_LIMIT_QUERY,
    validate_and_guard,
)


# ---------------------------------------------------------------------------
# 校验器：合法查询放行
# ---------------------------------------------------------------------------

def test_valid_select_passes_with_auto_limit():
    guarded, reason = validate_and_guard("SELECT hostname, status FROM assets WHERE reachable = 1")
    assert reason is None
    assert f"LIMIT {MAX_LIMIT_QUERY}" in guarded
    assert "MAX_EXECUTION_TIME" in guarded


def test_bare_browse_limit_tightened():
    guarded, reason = validate_and_guard("SELECT * FROM assets")
    assert reason is None
    assert f"LIMIT {MAX_LIMIT_BROWSE}" in guarded  # 无 WHERE 无聚合 → 收紧 200


def test_limit_over_cap_converged():
    guarded, reason = validate_and_guard(
        "SELECT hostname FROM assets WHERE status = 'child' LIMIT 99999"
    )
    assert reason is None
    assert f"LIMIT {MAX_LIMIT_QUERY}" in guarded
    assert "99999" not in guarded


def test_aggregate_without_where_allowed():
    guarded, reason = validate_and_guard("SELECT kind, count(*) AS cnt FROM assets GROUP BY kind")
    assert reason is None
    assert "GROUP BY" in guarded.upper()


def test_current_db_prefix_allowed_and_other_db_blocked():
    guarded, reason = validate_and_guard(f"SELECT hostname FROM {nl2sql.current_database()}.assets LIMIT 10")
    assert reason is None
    guarded, reason = validate_and_guard("SELECT hostname FROM other_business.assets LIMIT 10")
    assert reason is not None and "跨库" in reason


def test_union_of_whitelisted_tables_passes():
    guarded, reason = validate_and_guard(
        "SELECT hostname FROM assets UNION SELECT name FROM owner_contacts LIMIT 10"
    )
    assert reason is None


def test_cte_over_whitelisted_table_passes():
    guarded, reason = validate_and_guard(
        "WITH t AS (SELECT kind FROM assets) SELECT kind, count(*) AS cnt FROM t GROUP BY kind LIMIT 10"
    )
    assert reason is None


# ---------------------------------------------------------------------------
# 校验器：非业务表 / 系统库拦截
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT username FROM users LIMIT 10",
        "SELECT * FROM roles LIMIT 10",
        "SELECT * FROM permissions LIMIT 10",
        "SELECT * FROM user_roles LIMIT 10",
        "SELECT * FROM role_permissions LIMIT 10",
        "SELECT * FROM menus LIMIT 10",
        "SELECT * FROM role_menus LIMIT 10",
        "SELECT * FROM user_tokens LIMIT 10",
        "SELECT * FROM login_logs LIMIT 10",
        "SELECT * FROM app_settings",  # AI API Key 存储表
        "SELECT * FROM some_random_table LIMIT 10",
    ],
)
def test_denied_tables_blocked(sql):
    guarded, reason = validate_and_guard(sql)
    assert reason is not None
    assert "禁止" in reason or "白名单" in reason


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM information_schema.tables LIMIT 10",
        "SELECT * FROM mysql.user LIMIT 10",
        "SELECT * FROM performance_schema.events_statements_summary_by_digest LIMIT 10",
        "SELECT * FROM sys.host_summary LIMIT 10",
    ],
)
def test_system_databases_blocked(sql):
    _, reason = validate_and_guard(sql)
    assert reason is not None and ("系统库" in reason or "白名单" in reason or "禁止" in reason)


def test_crafty_cte_over_denied_table_blocked():
    _, reason = validate_and_guard(
        "WITH t AS (SELECT username FROM users) SELECT * FROM t LIMIT 10"
    )
    assert reason is not None and "users" in reason


# ---------------------------------------------------------------------------
# 校验器：只读与注入防护拦截
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO assets(hostname) VALUES ('x')",
        "UPDATE assets SET status = 'x'",
        "DELETE FROM tickets",
        "DROP TABLE assets",
        "ALTER TABLE assets ADD COLUMN x INT",
        "TRUNCATE TABLE assets",
        "CREATE TABLE evil(id INT)",
        "CALL some_procedure()",
    ],
)
def test_write_statements_blocked(sql):
    _, reason = validate_and_guard(sql)
    assert reason is not None and "仅允许" in reason or reason is not None


def test_multi_statement_blocked():
    _, reason = validate_and_guard("SELECT 1 FROM assets; DROP TABLE users")
    assert reason is not None and "单条" in reason


def test_sleep_benchmark_loadfile_blocked():
    for sql in (
        "SELECT SLEEP(10) FROM assets LIMIT 1",
        "SELECT * FROM assets WHERE SLEEP(1) = 0 LIMIT 1",
        "SELECT BENCHMARK(5000000, MD5('x')) LIMIT 1",
        "SELECT LOAD_FILE('/etc/passwd') LIMIT 1",
    ):
        _, reason = validate_and_guard(sql)
        assert reason is not None and "危险函数" in reason


def test_into_outfile_blocked():
    _, reason = validate_and_guard("SELECT hostname FROM assets INTO OUTFILE '/tmp/x' LIMIT 10")
    assert reason is not None and "OUTFILE" in reason


def test_for_update_blocked():
    _, reason = validate_and_guard("SELECT hostname FROM assets WHERE id = 1 FOR UPDATE")
    assert reason is not None and "加锁" in reason


def test_placeholder_blocked():
    _, reason = validate_and_guard("SELECT * FROM assets WHERE hostname = ?")
    assert reason is not None and "占位符" in reason


def test_comment_obfuscation_still_blocked():
    _, reason = validate_and_guard("SELECT/*!*/username/*!*/FROM/*!*/users/*!*/LIMIT 10")
    assert reason is not None


def test_unparseable_sql_blocked():
    _, reason = validate_and_guard("SELECT FROM FROM ))(( **")
    assert reason is not None


# ---------------------------------------------------------------------------
# 只读执行：MySQL 分支事务序列（只读事务兜底）
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self):
        self.calls: list[str] = []

    def exec_driver_sql(self, sql: str):
        self.calls.append(sql)

        class _R:
            def keys(self):
                return ["hostname"]

            def fetchall(self):
                return [("node-1",)]

        return _R()


def test_mysql_readonly_transaction_sequence():
    conn = _FakeConn()
    cols, rows = nl2sql._execute_mysql_readonly(conn, "SELECT hostname FROM assets LIMIT 5")
    assert cols == ["hostname"] and rows == [["node-1"]]
    # 必须以只读事务开始、且最终 ROLLBACK 还原（连接归还池后无只读残留）
    assert conn.calls[0] == "START TRANSACTION READ ONLY"
    assert conn.calls[1] == "SELECT hostname FROM assets LIMIT 5"
    assert conn.calls[-1] == "ROLLBACK"


def test_mysql_readonly_rollback_on_error():
    class _BoomConn(_FakeConn):
        def exec_driver_sql(self, sql: str):
            self.calls.append(sql)
            if sql.startswith("START") or sql.startswith("ROLLBACK"):
                return None
            raise RuntimeError("boom")

    conn = _BoomConn()
    with pytest.raises(RuntimeError):
        nl2sql._execute_mysql_readonly(conn, "SELECT boom")
    assert conn.calls[-1] == "ROLLBACK"


# ---------------------------------------------------------------------------
# API 集成（sqlite 测试库 + mock 兜底路径）
# ---------------------------------------------------------------------------

def test_data_query_mock_keyword_ok(client):
    resp = client.post("/api/v1/knowledge/data-query", json={"question": "现在有多少台资产？"})
    assert resp.status_code == 200
    data = resp.json()
    assert "assets" in data["sql"]
    assert data["row_count"] >= 1
    assert isinstance(data["rows"], list) and data["rows"]
    assert data["answer"]  # 降级模板也有输出


def test_data_query_mock_anomaly(client):
    resp = client.post("/api/v1/knowledge/data-query", json={"question": "当前有哪些异常告警"})
    assert resp.status_code == 200
    data = resp.json()
    assert "anomaly_events" in data["sql"]
    assert "abnormal" in data["sql"]


def test_data_query_blocked_non_business_table(client, monkeypatch):
    monkeypatch.setattr(nl2sql, "generate_sql", lambda q, s, db=None: ("SELECT username, password_hash FROM users LIMIT 5", "llm"))
    resp = client.post("/api/v1/knowledge/data-query", json={"question": "列出所有用户"})
    assert resp.status_code == 400
    assert "安全审计拦截" in resp.json()["detail"]
    assert "users" in resp.json()["detail"]


def test_data_query_blocked_ai_key_table(client, monkeypatch):
    monkeypatch.setattr(nl2sql, "generate_sql", lambda q, s, db=None: ("SELECT * FROM app_settings LIMIT 5", "llm"))
    resp = client.post("/api/v1/knowledge/data-query", json={"question": "AI 配置是什么"})
    assert resp.status_code == 400
    assert "app_settings" in resp.json()["detail"]


def test_data_query_blocked_cross_database(client, monkeypatch):
    monkeypatch.setattr(nl2sql, "generate_sql", lambda q, s: ("SELECT * FROM finance_db.assets LIMIT 5", "llm"))
    resp = client.post("/api/v1/knowledge/data-query", json={"question": "看看财务系统的数据 xyz"})
    assert resp.status_code == 400
    assert "跨库" in resp.json()["detail"]


def test_data_query_blocked_write_sql(client, monkeypatch):
    monkeypatch.setattr(nl2sql, "generate_sql", lambda q, s: ("DELETE FROM tickets WHERE id = 1", "llm"))
    resp = client.post("/api/v1/knowledge/data-query", json={"question": "帮我处理一下那个单据 xyz"})
    assert resp.status_code == 400
    assert "安全审计拦截" in resp.json()["detail"]


def test_data_query_blocked_multi_statement(client, monkeypatch):
    monkeypatch.setattr(
        nl2sql, "generate_sql", lambda q, s, db=None: ("SELECT hostname FROM assets LIMIT 1; DROP TABLE users", "llm")
    )
    resp = client.post("/api/v1/knowledge/data-query", json={"question": "随便查点"})
    assert resp.status_code == 400
    assert "单条" in resp.json()["detail"]


def test_data_query_passes_db_to_llm(client, monkeypatch):
    """回归：NL2SQL 必须把 db 透传给 chat_completion。

    AI 网关配置存于 app_settings（系统管理页可改）；不传 db 只读环境变量，
    生产环境恒抛「未配置 AI API Key」→ 前端 502（2026-10-14 修复）。
    """
    captured = {}

    def fake_chat_completion(messages, max_tokens=0, db=None):
        captured["db"] = db
        return "SELECT kind, count(*) AS cnt FROM assets GROUP BY kind LIMIT 100"

    monkeypatch.setattr(nl2sql, "chat_completion", fake_chat_completion)
    # 问题不命中 mock 关键词，走 LLM 生成 SQL 路径
    resp = client.post("/api/v1/knowledge/data-query", json={"question": "最近一周每台子机的平均负载是多少"})
    assert resp.status_code == 200, resp.text
    assert captured["db"] is not None
    assert "assets" in resp.json()["sql"]


def test_data_query_requires_auth():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:  # 无 Authorization 头
        resp = c.post("/api/v1/knowledge/data-query", json={"question": "有多少台资产"})
        assert resp.status_code in (401, 403)
