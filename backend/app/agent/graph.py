from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.llm import diagnose
from app.agent.rag import search_runbooks
from app.agent.tools import gather_evidence
from app.domain.safety import parse_diagnosis


class InvestigationState(TypedDict, total=False):
    ticket_id: int
    asset_id: str
    tenant_id: str
    asset_tenant_id: str
    db_ok: bool
    alert: dict[str, Any]
    host_hint: dict[str, Any]
    event_id: str
    rag_hits: list[dict[str, Any]]
    evidence: dict[str, Any]
    diagnosis: dict[str, Any]
    model_version: str


def _rag_node(state: InvestigationState) -> InvestigationState:
    alert = state["alert"]
    query = f"{alert.get('trigger_name','')} {alert.get('message','')} {alert.get('demo_scenario','')}"
    hits = search_runbooks(query)
    return {"rag_hits": hits}


def _gather_node(state: InvestigationState) -> InvestigationState:
    alert = state["alert"]
    evidence = gather_evidence(
        asset_id=state["asset_id"],
        tenant_id=state["tenant_id"],
        asset_tenant_id=state["asset_tenant_id"],
        scenario=alert.get("demo_scenario") or "green",
        trigger=alert.get("trigger_name") or "",
        db_ok=state.get("db_ok", True),
        rag_hits=state.get("rag_hits") or [],
        host_hint=state.get("host_hint") or {},
        event_id=state.get("event_id") or alert.get("event_id") or "",
    )
    return {"evidence": evidence}


def _diagnose_node(state: InvestigationState) -> InvestigationState:
    diag, model_version = diagnose(state["alert"], state["evidence"])
    parsed = parse_diagnosis(diag.model_dump())
    return {"diagnosis": parsed.model_dump(), "model_version": model_version}


def build_graph():
    graph = StateGraph(InvestigationState)
    graph.add_node("rag", _rag_node)
    graph.add_node("gather", _gather_node)
    graph.add_node("diagnose", _diagnose_node)
    graph.add_edge(START, "rag")
    graph.add_edge("rag", "gather")
    graph.add_edge("gather", "diagnose")
    graph.add_edge("diagnose", END)
    return graph.compile()


_GRAPH = None


def run_investigation(state: InvestigationState) -> InvestigationState:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH.invoke(state)
