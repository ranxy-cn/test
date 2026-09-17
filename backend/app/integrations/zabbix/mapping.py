from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Asset


def mapping_from_asset(asset: Asset | None, webhook: dict[str, Any] | None = None) -> dict[str, str]:
    extra = (getattr(asset, "extra", None) or {}) if asset is not None else {}
    hook = webhook or {}
    return {
        "asset_id": getattr(asset, "id", "") or "",
        "hostname": getattr(asset, "hostname", None) or extra.get("hostname") or "",
        "external_id": str(
            getattr(asset, "external_id", None)
            or extra.get("external_id")
            or extra.get("zabbix_hostid")
            or hook.get("hostid")
            or ""
        ),
        "zabbix_host": str(
            getattr(asset, "zabbix_host", None)
            or extra.get("zabbix_host")
            or hook.get("host")
            or ""
        ),
        "webhook_host": str(hook.get("host") or hook.get("hostname") or ""),
        "webhook_hostid": str(hook.get("hostid") or ""),
        "webhook_hostname": str(hook.get("hostname") or ""),
    }


def has_mapping_hints(hint: dict[str, Any] | None) -> bool:
    if not hint:
        return False
    keys = ("external_id", "zabbix_host", "hostname", "webhook_hostid", "webhook_host", "webhook_hostname")
    return any(str(hint.get(k) or "").strip() for k in keys)


def find_asset_by_zabbix(
    db: Session,
    *,
    asset_id: str | None = None,
    host: str | None = None,
    hostname: str | None = None,
    hostid: str | None = None,
) -> Asset | None:
    if asset_id:
        row = db.get(Asset, asset_id)
        if row is not None:
            return row
    clauses = []
    if hostid:
        clauses.append(Asset.external_id == str(hostid))
    for name in (host, hostname):
        if not name:
            continue
        clauses.append(Asset.zabbix_host == name)
        clauses.append(Asset.hostname == name)
    if not clauses:
        return None
    return db.scalar(select(Asset).where(or_(*clauses)))
