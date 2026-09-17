#!/usr/bin/env python3
"""探测真实 Zabbix 只读 API（不会写配置/应答/脚本）。凭据只读环境变量，不落盘。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.integrations import clear_integration_probe_cache, describe_integrations  # noqa: E402
from app.integrations.zabbix.http import build_http_zabbix  # noqa: E402
from app.integrations.zabbix.methods import READ_ONLY_METHODS  # noqa: E402


def main() -> int:
    get_settings.cache_clear()
    clear_integration_probe_cache()
    settings = get_settings()
    if not settings.zabbix_url:
        print("未配置 ZABBIX_URL。demo 请保持 INTEGRATION_MODE=mock。")
        print("5.0 示例：ZABBIX_URL=http://zabbix.example/api_jsonrpc.php ZABBIX_USER=Admin ZABBIX_PASSWORD=*** ZABBIX_MODE=auto")
        return 2
    if not (settings.zabbix_token or (settings.zabbix_user and settings.zabbix_password)):
        print("缺少 ZABBIX_TOKEN 或 ZABBIX_USER/ZABBIX_PASSWORD")
        return 2

    factory = describe_integrations()
    zabbix_info = factory.get("zabbix") or {}
    print("只读方法白名单:", ", ".join(sorted(READ_ONLY_METHODS)))
    print(
        json.dumps(
            {
                "url": settings.zabbix_url,
                "user": settings.zabbix_user or None,
                "token_configured": bool(settings.zabbix_token),
                "requested_mode": zabbix_info.get("requested"),
                "effective_mode": zabbix_info.get("mode"),
                "fallback_reason": zabbix_info.get("fallback_reason"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    client = build_http_zabbix()
    health = client.health()
    print("--- health ---")
    print(json.dumps(health, ensure_ascii=False, indent=2))
    if not health.get("ok"):
        client.logout()
        return 1

    hosts = client.list_hosts(limit=20)
    problems = client.list_problems(limit=100)
    host_summary = [
        {"hostid": h.get("hostid"), "host": h.get("host"), "name": h.get("name"), "status": h.get("status")}
        for h in hosts
    ]
    print("--- hosts ---")
    print(json.dumps({"count": len(host_summary), "items": host_summary}, ensure_ascii=False, indent=2))
    print("--- problems ---")
    print(json.dumps({"count": len(problems)}, ensure_ascii=False, indent=2))
    client.logout()
    print("连通验证成功（已 user.logout，未执行任何写操作）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
