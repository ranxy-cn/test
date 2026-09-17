#!/usr/bin/env python3
"""真实 Zabbix 5.0 告警 → DevOpsAgent 工单端到端。

凭据只读环境变量，禁止写入仓库：
  ZABBIX_URL / ZABBIX_USER / ZABBIX_PASSWORD / ZABBIX_MODE=real

无 ZABBIX_URL 时清晰失败。未配置密码时：公开 apiinfo.version + 5.0 webhook 注入，
并标明「未配置密码，采证未走 real login」。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TERMINAL_STATUSES = {"recovered", "pending_approval", "escalated"}
STATUS_ALIAS = {"pending_approval": "waiting_approval"}
USAGE = """未配置 ZABBIX_URL。真实告警 e2e 需要：
  export ZABBIX_URL=http://124.221.251.186:8081/api_jsonrpc.php
  export ZABBIX_USER=Admin
  export ZABBIX_PASSWORD=***    # 本地填写，不要写入仓库
  export ZABBIX_MODE=real
  docker compose up --build -d
  ./scripts/e2e_real_zabbix.sh
无凭据时请改用 scripts/demo.sh（纯 mock）。"""


def env_get(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def has_login_creds(env: dict[str, str] | None = None) -> bool:
    data = env if env is not None else os.environ
    token = (data.get("ZABBIX_TOKEN") or "").strip()
    user = (data.get("ZABBIX_USER") or "").strip()
    password = (data.get("ZABBIX_PASSWORD") or "").strip()
    return bool(token or (user and password))


def missing_env_message(env: dict[str, str] | None = None) -> str | None:
    data = env if env is not None else os.environ
    url = (data.get("ZABBIX_URL") or "").strip()
    mode = (data.get("ZABBIX_MODE") or "").strip().lower()
    if not url:
        return USAGE
    if mode not in {"real", "auto"}:
        return (
            "请设置 ZABBIX_MODE=real（当前为空或 mock）。"
            "无真实 Zabbix 凭据时请改用 scripts/demo.sh。"
        )
    return None


def _http_json(method: str, url: str, *, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 15.0) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/json")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 {url}: {exc.reason}") from exc


def nseverity_to_text(value: str) -> str:
    return {
        "0": "Not classified",
        "1": "Information",
        "2": "Warning",
        "3": "Average",
        "4": "High",
        "5": "Disaster",
    }.get(str(value), "High")


def build_zabbix50_webhook(
    *,
    eventid: str,
    host: str,
    hostname: str,
    hostid: str,
    trigger: str,
    triggerid: str = "",
    severity: str = "4",
    value: str = "1",
    message: str = "",
    clock: str | int | None = None,
    item_name: str = "",
    item_key: str = "",
    item_value: str = "",
) -> dict[str, Any]:
    severity_text = nseverity_to_text(severity) if str(severity).isdigit() else (severity or "High")
    nseverity = severity if str(severity).isdigit() else "4"
    payload: dict[str, Any] = {
        "eventid": str(eventid),
        "EVENT.ID": str(eventid),
        "EVENT.NAME": message or trigger,
        "EVENT.STATUS": "PROBLEM",
        "EVENT.VALUE": str(value),
        "EVENT.SEVERITY": severity_text,
        "EVENT.NSEVERITY": str(nseverity),
        "host": host,
        "hostname": hostname or host,
        "hostid": str(hostid),
        "HOST.HOST": host,
        "HOST.NAME": hostname or host,
        "HOST.ID": str(hostid),
        "trigger": trigger,
        "TRIGGER.NAME": trigger,
        "triggerid": str(triggerid or ""),
        "TRIGGER.ID": str(triggerid or ""),
        "severity": severity_text,
        "value": "PROBLEM",
        "message": message or trigger,
        "clock": clock or int(time.time()),
    }
    if item_name:
        payload["ITEM.NAME"] = item_name
        payload["item_name"] = item_name
    if item_key:
        payload["ITEM.KEY"] = item_key
    if item_value:
        payload["ITEM.VALUE"] = item_value
    return payload


def evidence_is_real(ticket: dict[str, Any]) -> bool:
    evidence = ticket.get("evidence") or {}
    metrics = evidence.get("metrics") or {}
    events = evidence.get("events") or {}
    if metrics.get("source") != "zabbix-http" or not metrics.get("mapped"):
        return False
    if events.get("source") != "zabbix-http" or not events.get("mapped"):
        return False
    host = metrics.get("host") or events.get("host") or {}
    has_host = bool(host.get("hostid") or host.get("host"))
    has_items = bool(metrics.get("items_preview") or events.get("items"))
    has_problems = events.get("problems") is not None
    return has_host and has_items and has_problems


def pick_mapped_asset(assets: list[dict[str, Any]], hostid: str, host: str, hostname: str) -> dict[str, Any] | None:
    hostid = str(hostid or "")
    names = {n.lower() for n in (host, hostname) if n}
    for row in assets:
        if hostid and str(row.get("external_id") or "") == hostid:
            return row
        if (row.get("zabbix_host") or "").lower() in names or (row.get("hostname") or "").lower() in names:
            return row
    return None


def wait_terminal(base: str, ticket_id: int, timeout: int, headers: dict[str, str] | None = None) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    seen: list[str] = []
    while time.time() < deadline:
        last = _http_json("GET", f"{base}/api/v1/tickets/{ticket_id}", headers=headers, timeout=20)
        status = (last.get("ticket") or {}).get("status") or ""
        if not seen or seen[-1] != status:
            seen.append(status)
            print(f"  ticket {ticket_id} -> {status}" + (f" ({STATUS_ALIAS[status]})" if status in STATUS_ALIAS else ""))
        if status in TERMINAL_STATUSES:
            last["_transitions"] = seen
            return last
        time.sleep(2)
    last["_transitions"] = seen
    raise TimeoutError(f"等待终态超时（{timeout}s），最后状态={(last.get('ticket') or {}).get('status')}")


def print_json(title: str, payload: Any) -> None:
    print(f"--- {title} ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def workbench_login(base: str) -> dict[str, str]:
    user = env_get("ADMIN_USERNAME") or "admin"
    password = env_get("ADMIN_PASSWORD") or "admin"
    body = _http_json(
        "POST",
        f"{base}/api/v1/auth/login",
        payload={"username": user, "password": password},
        timeout=15,
    )
    token = (body or {}).get("access_token") or ""
    if not token:
        raise RuntimeError("工作台登录失败（检查 ADMIN_USERNAME / ADMIN_PASSWORD）")
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    err = missing_env_message()
    if err:
        print(err)
        return 2

    url = env_get("ZABBIX_URL")
    user = env_get("ZABBIX_USER")
    mode = env_get("ZABBIX_MODE") or "real"
    base = (env_get("DEVOPS_API_BASE") or env_get("BASE") or "http://127.0.0.1:8000").rstrip("/")
    secret = env_get("WEBHOOK_SECRET") or "dev-webhook-secret"
    timeout = int(env_get("E2E_TIMEOUT_SECONDS") or "180")
    login = has_login_creds()
    injected = False
    no_login_note = "未配置密码，采证未走 real login"

    print("== 真实 Zabbix 告警 e2e ==")
    print(
        json.dumps(
            {
                "zabbix_url": url,
                "zabbix_user": user or None,
                "token_configured": bool(env_get("ZABBIX_TOKEN")),
                "zabbix_mode": mode,
                "login_configured": login,
                "api_base": base,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    from app.integrations.zabbix.http import HttpZabbixClient, normalize_zabbix_jsonrpc_url

    client = HttpZabbixClient(
        url,
        env_get("ZABBIX_TOKEN"),
        username=user,
        password=env_get("ZABBIX_PASSWORD"),
        timeout=8.0,
        retries=2,
        verify_ssl=(env_get("ZABBIX_VERIFY_SSL") or "true").lower() not in {"0", "false", "no"},
    )
    print(f"JSON-RPC: {normalize_zabbix_jsonrpc_url(url)}")

    try:
        version = client.api_version()
        print(f"apiinfo.version = {version}（无需登录）")
    except Exception as exc:  # noqa: BLE001
        print(f"apiinfo.version 失败: {exc}")
        if not login:
            print(no_login_note)
            return 1
        print("继续尝试带登录的 health…")
        version = ""

    hosts: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    chosen: dict[str, Any] | None = None
    if login:
        health = client.health()
        print_json("health", health)
        if not health.get("ok"):
            client.logout()
            print("Zabbix 登录/host.get 失败，无法走 real 采证。")
            return 1
        hosts = client.list_hosts(limit=50)
        problems = client.list_problem_events(limit=50)
        print_json("hosts", {"count": len(hosts), "items": [{"hostid": h.get("hostid"), "host": h.get("host"), "name": h.get("name")} for h in hosts[:10]]})
        print_json("problems", {"count": len(problems), "items": problems[:5]})
        if problems:
            chosen = problems[0]
            print("使用当前真实 problem.get 问题立案。")
        elif hosts:
            host = hosts[0]
            chosen = {
                "eventid": f"e2e-inject-{int(time.time())}",
                "name": "CPU usage > 85% for 5 minutes",
                "severity": "4",
                "clock": int(time.time()),
                "objectid": "",
                "value": "1",
                "hostid": str(host.get("hostid") or ""),
                "host": host.get("host") or "",
                "hostname": host.get("name") or host.get("host") or "",
            }
            injected = True
            print("当前无打开的 problem，改为注入一条 5.0 webhook 宏兼容告警；证据阶段用 real API 补全 host/items/problems。")
        client.logout()
    else:
        print(no_login_note)
        print("将注入 5.0 webhook（默认 Zabbix server / 10084），仅用公开 apiinfo.version 做探测。")
        chosen = {
            "eventid": f"e2e-inject-{int(time.time())}",
            "name": "CPU usage > 85% for 5 minutes",
            "severity": "4",
            "clock": int(time.time()),
            "objectid": "",
            "value": "1",
            "hostid": "10084",
            "host": "Zabbix server",
            "hostname": "Zabbix server",
        }
        injected = True

    try:
        health_api = _http_json("GET", f"{base}/health")
    except Exception as exc:  # noqa: BLE001
        print(f"本服务 API 不可达（{base}）：{exc}")
        print("请先 docker compose up，并确认已把 ZABBIX_* 传给 api/worker。")
        return 1
    print_json("devops health", health_api)
    try:
        workbench = workbench_login(base)
    except Exception as exc:  # noqa: BLE001
        print(f"工作台登录失败: {exc}")
        print("请确认 ADMIN_USERNAME / ADMIN_PASSWORD（演示默认见 .env.example）。")
        return 1
    try:
        status = _http_json("GET", f"{base}/api/v1/status", headers=workbench)
        print_json("devops integrations", (status.get("integrations") or {}).get("zabbix") or status)
    except Exception as exc:  # noqa: BLE001
        print(f"读取 /api/v1/status 失败: {exc}")
        status = {}

    assets = []
    try:
        assets = (_http_json("GET", f"{base}/api/v1/assets", headers=workbench) or {}).get("items") or []
    except Exception as exc:  # noqa: BLE001
        print(f"读取资产列表失败: {exc}")

    assert chosen is not None
    hostid = str(chosen.get("hostid") or "")
    host = str(chosen.get("host") or "")
    hostname = str(chosen.get("hostname") or host)
    mapped = pick_mapped_asset(assets, hostid, host, hostname)
    webhook = build_zabbix50_webhook(
        eventid=str(chosen.get("eventid")),
        host=host or "Zabbix server",
        hostname=hostname or "Zabbix server",
        hostid=hostid or "10084",
        trigger=str(chosen.get("name") or "CPU usage > 85% for 5 minutes"),
        triggerid=str(chosen.get("objectid") or ""),
        severity=str(chosen.get("severity") or "4"),
        value=str(chosen.get("value") or "1"),
        message=str(chosen.get("name") or ""),
        clock=chosen.get("clock"),
    )
    if mapped is None:
        webhook["asset_id"] = "ast-zabbix-server"
        print("CMDB 未直接命中该 host，回退 asset_id=ast-zabbix-server；resolve_host 仍优先 webhook HOST.ID。")
    else:
        print(f"CMDB 映射: {mapped.get('id')} ← host={host} hostid={hostid}")

    print_json("webhook payload keys", sorted(webhook.keys()))
    created = _http_json(
        "POST",
        f"{base}/api/v1/webhooks/zabbix",
        payload=webhook,
        headers={"X-Webhook-Secret": secret},
        timeout=30,
    )
    ticket = created.get("ticket") or {}
    ticket_id = ticket.get("id")
    if not ticket_id:
        print_json("webhook 响应", created)
        print("立案失败。")
        return 1
    print(f"已立案 ticket_id={ticket_id} number={ticket.get('number')} status={ticket.get('status')}")

    try:
        detail = wait_terminal(base, int(ticket_id), timeout, headers=workbench)
    except TimeoutError as exc:
        print(str(exc))
        return 1

    ticket = detail.get("ticket") or {}
    events = detail.get("events") or []
    audits = detail.get("audit") or []
    real = evidence_is_real(ticket)
    metrics = (ticket.get("evidence") or {}).get("metrics") or {}
    ev = (ticket.get("evidence") or {}).get("events") or {}
    status_name = ticket.get("status")
    print_json(
        "summary",
        {
            "ticket_id": ticket.get("id"),
            "number": ticket.get("number"),
            "status": status_name,
            "status_alias": STATUS_ALIAS.get(status_name, status_name),
            "transitions": detail.get("_transitions") or [e.get("kind") for e in events],
            "event_kinds": [e.get("kind") for e in events],
            "injected_webhook": injected,
            "login_configured": login,
            "evidence_from_real_zabbix": real,
            "evidence_metrics_source": metrics.get("source"),
            "evidence_events_source": ev.get("source"),
            "evidence_mapped": metrics.get("mapped"),
            "evidence_host": metrics.get("host") or ev.get("host"),
            "evidence_items_preview": (metrics.get("items_preview") or [])[:3],
            "evidence_problems_count": len(ev.get("problems") or []),
            "policy_light": ticket.get("policy_light"),
            "policy_result": ticket.get("policy_result"),
            "candidate_action_id": ticket.get("candidate_action_id"),
            "audit_types": [a.get("event_type") for a in audits],
        },
    )
    if not login:
        print(no_login_note)
        print("部分路径完成：webhook 立案 + 状态机终态；采证未走 real login。")
        return 0
    backend_mode = ((status.get("integrations") or {}).get("zabbix") or {}).get("mode")
    if not real:
        print("ZABBIX_MODE=real 但工单证据未带 real API 的 host/items/problems。")
        print("请确认 docker compose 的 api/worker 已注入相同 ZABBIX_URL/USER/PASSWORD，且 ZABBIX_MODE=real。")
        if backend_mode and backend_mode != "real":
            print(f"当前后端 zabbix.mode={backend_mode}")
        return 1
    print("e2e 成功：真实 Zabbix 只读采证已写入任务单，终态可达审计。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
