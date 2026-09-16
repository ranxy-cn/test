from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.domain.safety import UnsafeExecutionError, validate_tool_call

INVESTIGATION_TOOLS = {
    "TOOL-METRICS-QUERY": {"window_minutes"},
    "TOOL-LOGS-READ": {"window_minutes", "max_lines", "level"},
    "TOOL-DEPLOY-HISTORY": {"limit"},
    "TOOL-DEPS-HEALTH": set(),
    "TOOL-RAG-SEARCH": {"query"},
}

REDACT_PATTERNS = [
    (re.compile(r"(?i)(password\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(authorization:\s*).+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(token\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(secret\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
]


def redact(text: str) -> str:
    out = text
    for pat, repl in REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _check(asset_id: str, action_id: str, params: dict, tenant_id: str, asset_tenant_id: str) -> None:
    allowed = INVESTIGATION_TOOLS.get(action_id)
    if allowed is None:
        raise UnsafeExecutionError(f"未知排查工具: {action_id}")
    extra = set(params) - allowed
    if extra:
        raise UnsafeExecutionError(f"排查工具参数非法: {sorted(extra)}")
    validate_tool_call(
        asset_id=asset_id,
        action_id=action_id,
        params=params,
        tenant_id=tenant_id,
        asset_tenant_id=asset_tenant_id,
        allow_investigation_tools=True,
    )


def query_metrics(asset_id: str, scenario: str, trigger: str) -> dict[str, Any]:
    cpu = 92.4 if "cpu" in trigger.lower() or scenario in {"green", "verify_fail", "cooldown"} else 41.0
    if scenario == "yellow":
        cpu = 28.0
    if scenario == "red":
        cpu = 67.0
    return {
        "ref": "metrics:cpu",
        "window_minutes": 30,
        "cpu_pct": cpu,
        "mem_pct": 71.2,
        "disk_pct": 38.0,
        "top_process": "java OrderApplication" if scenario != "yellow" else "mysqld",
        "series": [
            {"t": "-25m", "cpu": max(20, cpu - 40)},
            {"t": "-10m", "cpu": max(40, cpu - 10)},
            {"t": "now", "cpu": cpu},
        ],
    }


def read_logs(asset_id: str, scenario: str) -> dict[str, Any]:
    if scenario == "yellow":
        raw = (
            "2026-09-16 10:02:11 ERROR replication lag 128s source=order-db-01\n"
            "2026-09-16 10:02:40 WARN lock wait timeout password=SuperSecret123\n"
            "2026-09-16 10:03:01 ERROR Seconds_Behind_Source=128\n"
        )
    elif scenario == "red":
        raw = (
            "2026-09-16 10:04:12 FATAL native crash in unknown module\n"
            "2026-09-16 10:04:13 ERROR token=abcd.efgh.ijkl dumped core\n"
        )
    else:
        raw = (
            "2026-09-16 10:01:22 WARN  GC pause Full GC (Allocation Failure) 4.2s\n"
            "2026-09-16 10:02:01 WARN  GC pause Full GC 3.8s Authorization: Bearer prod-admin-key\n"
            "2026-09-16 10:02:40 INFO  order-service v2.4.1 request p99=1800ms\n"
        )
    redacted = redact(raw)
    lines = [ln for ln in redacted.splitlines() if ln][:100]
    return {
        "ref": "logs:gc" if scenario != "yellow" else "logs:repl",
        "max_lines": 100,
        "lines": lines,
        "redacted": True,
    }


def deploy_history(asset_id: str, scenario: str) -> dict[str, Any]:
    return {
        "ref": "deploy:last",
        "items": [
            {
                "at": "2026-09-15T22:00:00Z",
                "service": "order-service",
                "version": "v2.4.1",
                "actor": "ci-bot",
                "note": "夜间发版" if scenario != "yellow" else "配置变更 binlog 参数",
            }
        ],
    }


def dependency_health(asset_id: str, scenario: str, db_ok: bool) -> dict[str, Any]:
    db_status = "ok" if db_ok and scenario != "yellow" else "degraded"
    return {
        "ref": "deps:health",
        "items": [
            {
                "asset_id": "ast-order-db-01",
                "status": db_status,
                "detail": "replication lag" if scenario == "yellow" else "connect ok",
            },
            {"asset_id": "ast-order-redis-01", "status": "ok", "detail": "pong"},
        ],
    }


def gather_evidence(
    *,
    asset_id: str,
    tenant_id: str,
    asset_tenant_id: str,
    scenario: str,
    trigger: str,
    db_ok: bool,
    rag_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    jobs = {
        "metrics": (
            "TOOL-METRICS-QUERY",
            {"window_minutes": 30},
            lambda: query_metrics(asset_id, scenario, trigger),
        ),
        "logs": (
            "TOOL-LOGS-READ",
            {"window_minutes": 30, "max_lines": 100, "level": "WARN"},
            lambda: read_logs(asset_id, scenario),
        ),
        "deploys": ("TOOL-DEPLOY-HISTORY", {"limit": 5}, lambda: deploy_history(asset_id, scenario)),
        "deps": ("TOOL-DEPS-HEALTH", {}, lambda: dependency_health(asset_id, scenario, db_ok)),
    }
    evidence: dict[str, Any] = {
        "rag": {"ref": "runbook:RB-CPU-001" if rag_hits else "runbook:miss", "hits": rag_hits}
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {}
        for name, (action_id, params, fn) in jobs.items():
            _check(asset_id, action_id, params, tenant_id, asset_tenant_id)
            futs[pool.submit(fn)] = name
        for fut in as_completed(futs):
            evidence[futs[fut]] = fut.result()
    return evidence
