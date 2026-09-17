"""Zabbix JSON-RPC 只读方法白名单。写操作一律拒绝。"""

from __future__ import annotations

READ_ONLY_METHODS = frozenset(
    {
        "apiinfo.version",
        "user.login",
        "host.get",
        "item.get",
        "history.get",
        "trend.get",
        "event.get",
        "problem.get",
        "trigger.get",
    }
)

_WRITE_MARKERS = (
    "create",
    "update",
    "delete",
    "execute",
    "acknowledge",
    "import",
    "massadd",
    "massupdate",
    "massremove",
    "replace",
    "clear",
)


class ZabbixWriteRejected(PermissionError):
    """试图调用非只读 Zabbix 方法。"""


def assert_readonly_method(method: str) -> str:
    name = (method or "").strip()
    lowered = name.lower()
    if name not in READ_ONLY_METHODS:
        raise ZabbixWriteRejected(f"拒绝非只读 Zabbix 方法: {method}")
    suffix = lowered.rsplit(".", 1)[-1] if "." in lowered else lowered
    if suffix in _WRITE_MARKERS or any(tok in lowered for tok in ("script.execute", "configuration.", "action.create")):
        raise ZabbixWriteRejected(f"拒绝非只读 Zabbix 方法: {method}")
    return name
