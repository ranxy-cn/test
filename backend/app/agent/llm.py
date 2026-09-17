from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings
from app.domain.safety import never_give_secrets_to_llm, parse_diagnosis
from app.schemas import Diagnosis


SYSTEM_PROMPT = """你是智能巡检的分析模块，只做根因分析与预案推荐。
硬约束：
- 不要输出任何命令、shell、路径或凭据。
- candidate_action_id 只能是预案目录中的编号（如 ACT-ROLLING-RESTART），没有命中则设为 null。
- 只使用提供的证据引用。
- 输出 JSON：root_cause, evidence_refs, candidate_action_id, confidence, summary, recommended_params。
"""


def mock_diagnose(alert: dict[str, Any], evidence: dict[str, Any]) -> Diagnosis:
    scenario = (alert.get("demo_scenario") or "").lower()
    trigger = (alert.get("trigger_name") or "").lower()
    logs = "\n".join(evidence.get("logs", {}).get("lines") or []).lower()
    rag_hits = evidence.get("rag", {}).get("hits") or []

    if scenario == "yellow" or "replication" in trigger or "主备" in trigger or "replication" in logs:
        return Diagnosis(
            root_cause="订单库复制延迟升高并出现锁等待，疑似主库压力/同步中断",
            evidence_refs=["logs:repl", "deps:health", "deploy:last"],
            candidate_action_id="ACT-DB-FAILOVER",
            confidence=0.78,
            summary="命中高风险预案 ACT-DB-FAILOVER，必须审批后才能执行。",
            recommended_params={"force": False},
        )

    if scenario == "disk" or "disk" in trigger or "磁盘" in trigger or "临时日志" in trigger:
        return Diagnosis(
            root_cause="应用临时日志堆积导致磁盘使用率升高",
            evidence_refs=["metrics:disk", "runbook:RB-DISK-001"],
            candidate_action_id="ACT-CLEAN-TMPLOG",
            confidence=0.81,
            summary="命中低风险预案 ACT-CLEAN-TMPLOG。",
            recommended_params={"max_age_hours": 24},
        )

    if scenario == "probe" or "探针" in trigger or "probe" in trigger:
        return Diagnosis(
            root_cause="旁路业务探针进程异常退出",
            evidence_refs=["deps:health", "runbook:RB-DISK-001"],
            candidate_action_id="ACT-RESTART-PROBE",
            confidence=0.8,
            summary="命中低风险预案 ACT-RESTART-PROBE。",
            recommended_params={"probe_name": "biz-probe"},
        )

    if scenario == "red" or not rag_hits and ("native crash" in logs or "mystery" in trigger or "未知" in trigger):
        return Diagnosis(
            root_cause="出现未知原生崩溃，现有手册未覆盖该故障模式",
            evidence_refs=["logs:gc", "metrics:cpu"] if evidence.get("logs") else ["metrics:cpu"],
            candidate_action_id=None,
            confidence=0.34,
            summary="未命中预案白名单，建议携带证据升级人工。",
            recommended_params={},
        )

    refs = ["metrics:cpu", "logs:gc", "deploy:last"]
    if rag_hits:
        refs.append("runbook:RB-CPU-001")
    return Diagnosis(
        root_cause="昨晚发版后出现内存泄漏，频繁 Full GC 将 CPU 顶高",
        evidence_refs=refs,
        candidate_action_id="ACT-ROLLING-RESTART",
        confidence=0.86,
        summary="根因命中《CPU 飙高排查手册》，推荐滚动重启预案。",
        recommended_params={"batch_size": 1},
    )


def _openai_diagnose(alert: dict[str, Any], evidence: dict[str, Any]) -> Diagnosis:
    settings = get_settings()
    safe_evidence = never_give_secrets_to_llm(evidence)
    payload = {
        "model": settings.openai_model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"alert": never_give_secrets_to_llm(alert), "evidence": safe_evidence},
                    ensure_ascii=False,
                ),
            },
        ],
    }
    url = settings.openai_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    return parse_diagnosis(data)


def diagnose(alert: dict[str, Any], evidence: dict[str, Any]) -> tuple[Diagnosis, str]:
    settings = get_settings()
    if not settings.openai_api_key:
        return mock_diagnose(alert, evidence), settings.mock_model_version
    try:
        return _openai_diagnose(alert, evidence), settings.openai_model
    except Exception:
        return mock_diagnose(alert, evidence), settings.mock_model_version + "+fallback"
