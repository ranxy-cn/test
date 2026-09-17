from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations import get_zabbix_client
from app.integrations.zabbix.mapping import mapping_from_asset
from app.models import Asset, utcnow

UNMAPPED_ERROR = "未映射"
_STATUS_WORKERS = 8


def has_zabbix_identity(hint: dict[str, Any] | None) -> bool:
    """Live status maps via zabbix_host / external_id (hostid), not hostname alone."""
    if not hint:
        return False
    return any(str(hint.get(k) or "").strip() for k in ("zabbix_host", "external_id", "webhook_hostid"))


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def unmapped_metrics(*, updated_at: str, source: str = "unmapped", error: str | None = None) -> dict[str, Any]:
    return {
        "cpu_pct": None,
        "mem_pct": None,
        "disk_pct": None,
        "source": source,
        "updated_at": updated_at,
        "host_mapped": False,
        "error": error or UNMAPPED_ERROR,
    }


def shape_metrics(raw: dict[str, Any] | None, *, updated_at: str) -> dict[str, Any]:
    data = raw or {}
    mapped = bool(data.get("mapped"))
    source = str(data.get("source") or ("unmapped" if not mapped else "unknown"))
    error = data.get("last_error") or data.get("error")
    if not mapped:
        note = data.get("note")
        if not error:
            error = note if note else UNMAPPED_ERROR
        elif note and UNMAPPED_ERROR in str(note) and UNMAPPED_ERROR not in str(error):
            error = f"{UNMAPPED_ERROR}（{error}）"
        return unmapped_metrics(updated_at=updated_at, source=source, error=str(error))
    return {
        "cpu_pct": _num(data.get("cpu_pct")),
        "mem_pct": _num(data.get("mem_pct")),
        "disk_pct": _num(data.get("disk_pct")),
        "source": source,
        "updated_at": updated_at,
        "host_mapped": True,
        "error": str(error) if error else None,
    }


def _query_one(asset_id: str, hint: dict[str, Any], updated_at: str) -> dict[str, Any]:
    if not has_zabbix_identity(hint):
        return unmapped_metrics(updated_at=updated_at)
    try:
        raw = get_zabbix_client().query_metrics(
            asset_id,
            5,
            host_hint=hint,
            include_history=False,
        )
        return shape_metrics(raw, updated_at=updated_at)
    except Exception as exc:  # noqa: BLE001
        return unmapped_metrics(updated_at=updated_at, source="zabbix-degraded", error=str(exc))


def collect_asset_runtime_status(db: Session) -> dict[str, Any]:
    rows = db.scalars(select(Asset).order_by(Asset.id)).all()
    updated_at = utcnow().isoformat()
    snapshots = [
        {
            "id": a.id,
            "hostname": a.hostname,
            "zabbix_host": a.zabbix_host,
            "external_id": a.external_id,
            "hint": mapping_from_asset(a),
        }
        for a in rows
    ]
    metrics_by_id: dict[str, dict[str, Any]] = {}
    workers = min(_STATUS_WORKERS, max(1, len(snapshots)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(_query_one, snap["id"], snap["hint"], updated_at): snap["id"] for snap in snapshots
        }
        for fut in as_completed(futs):
            metrics_by_id[futs[fut]] = fut.result()
    return {
        "updated_at": updated_at,
        "items": [
            {
                "id": snap["id"],
                "hostname": snap["hostname"],
                "zabbix_host": snap["zabbix_host"],
                "external_id": snap["external_id"],
                "metrics": metrics_by_id[snap["id"]],
            }
            for snap in snapshots
        ],
    }
