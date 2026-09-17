#!/usr/bin/env python3
"""检测本机 Ansible runner / ansible-playbook 是否可用于白名单 Playbook。不打印密钥。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.integrations import describe_integrations  # noqa: E402
from app.integrations.ansible.real import playbook_binary, runner_importable  # noqa: E402
from app.integrations.ansible.safety import ansible_plays_dir  # noqa: E402


def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    plays = ansible_plays_dir()
    files = sorted(p.name for p in plays.glob("ACT-*.yml")) if plays.is_dir() else []
    info = describe_integrations().get("ansible") or {}
    payload = {
        "requested": info.get("requested"),
        "effective_mode": info.get("mode"),
        "fallback_reason": info.get("fallback_reason"),
        "runner_importable": runner_importable(),
        "ansible_playbook": playbook_binary(),
        "runner_enabled": bool(settings.ansible_runner_enabled),
        "check_mode": bool(settings.ansible_check_mode),
        "inventory": settings.ansible_inventory or None,
        "ssh_key_configured": bool(settings.ansible_ssh_private_key_file),
        "whitelist_plays": files,
        "hint": "无远端主机：ANSIBLE_CHECK_MODE=true 并复制 inventory/demo.ini.example",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if info.get("mode") == "real" and (runner_importable() or playbook_binary()):
        print("Ansible 真实路径可用（仅白名单 playbooks/ansible/*.yml）。")
        return 0
    print("当前不会走真实 Ansible（mock 或缺少 runner）。demo 仍可跑。")
    return 0 if info.get("mode") == "mock" else 1


if __name__ == "__main__":
    raise SystemExit(main())
