#!/usr/bin/env python3
"""探测真实 Zabbix 只读 API（不会写任何配置/应答/脚本）。无凭据时直接退出。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.integrations.zabbix.http import build_http_zabbix  # noqa: E402
from app.integrations.zabbix.methods import READ_ONLY_METHODS  # noqa: E402


def main() -> int:
    settings = get_settings()
    if not settings.zabbix_url:
        print("未配置 ZABBIX_URL。demo 请保持 INTEGRATION_MODE=mock。")
        print("示例：ZABBIX_URL=https://zabbix.example/api_jsonrpc.php ZABBIX_TOKEN=xxx ZABBIX_MODE=auto")
        return 2
    print("只读方法白名单:", ", ".join(sorted(READ_ONLY_METHODS)))
    client = build_http_zabbix()
    health = client.health()
    print(json.dumps(health, ensure_ascii=False, indent=2))
    return 0 if health.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
