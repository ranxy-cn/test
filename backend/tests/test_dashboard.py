"""Dashboard overview 数据准确性测试：排除孤儿子机。"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_dashboard_excludes_orphaned_children(client: TestClient, db):
    """孤儿子机（kind=child 且无 mother_id）不应计入大屏 KPI。"""
    from app.models import Asset

    def _asset(id: str, **kw) -> Asset:
        return Asset(id=id, hostname=id, app="demo", role="node", owner="qa", tenant_id="t1", **kw)

    db.add(_asset("mother-1", kind="mother", reachable=True))
    db.add(_asset("child-1", kind="child", mother_id="mother-1", reachable=True))
    db.add(_asset("orphan-1", kind="child", mother_id="", reachable=False))
    db.commit()

    resp = client.get("/api/v1/dashboard/overview")
    assert resp.status_code == 200
    data = resp.json()
    kpis = data["kpis"]

    assert kpis["asset_total"] == 2
    assert kpis["asset_reachable"] == 2
    assert kpis["availability"] == 100.0
    hostnames = [asset["hostname"] for asset in data["assets"]]
    assert "orphan-1" not in hostnames
