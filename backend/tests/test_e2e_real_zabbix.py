from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "e2e_real_zabbix.py"


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ZABBIX_")}
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    env["PYTHONPATH"] = str(ROOT / "backend")
    return env


def test_e2e_script_fails_without_zabbix_url():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=_clean_env(),
        cwd=str(ROOT),
        check=False,
    )
    assert proc.returncode == 2
    combined = proc.stdout + proc.stderr
    assert "ZABBIX_URL" in combined
    assert "demo.sh" in combined


def test_e2e_script_fails_when_mode_not_real():
    env = _clean_env()
    env["ZABBIX_URL"] = "http://127.0.0.1:9/api_jsonrpc.php"
    env["ZABBIX_MODE"] = "mock"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        check=False,
    )
    assert proc.returncode == 2
    assert "ZABBIX_MODE" in (proc.stdout + proc.stderr)


def test_e2e_helpers_no_login_without_password():
    sys.path.insert(0, str(ROOT / "scripts"))
    import e2e_real_zabbix as e2e  # noqa: E402

    assert e2e.has_login_creds({"ZABBIX_USER": "Admin", "ZABBIX_PASSWORD": ""}) is False
    assert e2e.has_login_creds({"ZABBIX_USER": "Admin", "ZABBIX_PASSWORD": "x"}) is True
    payload = e2e.build_zabbix50_webhook(
        eventid="1",
        host="Zabbix server",
        hostname="Zabbix server",
        hostid="10084",
        trigger="CPU usage too high",
        severity="4",
    )
    assert payload["EVENT.ID"] == "1"
    assert payload["HOST.ID"] == "10084"
    assert payload["EVENT.SEVERITY"] == "High"
    assert e2e.evidence_is_real({"evidence": {"metrics": {"source": "mock"}}}) is False
    assert (
        e2e.evidence_is_real(
            {
                "evidence": {
                    "metrics": {
                        "source": "zabbix-http",
                        "mapped": True,
                        "host": {"hostid": "10084"},
                        "items_preview": [{"key": "agent.ping"}],
                    },
                    "events": {
                        "source": "zabbix-http",
                        "mapped": True,
                        "host": {"hostid": "10084"},
                        "items": [{"eventid": "1"}],
                        "problems": [{"eventid": "1"}],
                    },
                }
            }
        )
        is True
    )
