"""负责人告警推送：联系人 upsert/校验、告警渠道推送、AI 分析推送、webhook 联动。

短信渠道走 stub（供应商未对接，消息生成+留痕，不算失败）——后续接入供应商即生效。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import AiAnalysis, AnomalyEvent, Asset, Notification, OwnerContact, utcnow
from app.services.alert_engine import run_alert_cycle
from app.services import owner_notify


def _mk_child(db, asset_id: str, owner: str, extra: dict | None = None) -> None:
    db.add(
        Asset(
            id=asset_id,
            hostname=asset_id,
            app="",
            role="app",
            owner=owner,
            kind="child",
            mother_id="",
            tenant_id="tenant-default",
            extra=dict(extra or {}),
        )
    )
    db.commit()


def _policy(**over) -> dict:
    base = {"cpu_threshold": 50, "cpu_window_seconds": 60}
    base.update(over)
    return base


def _contact(db, name: str, channels: list[str], **kw) -> None:
    db.add(OwnerContact(name=name, channels=channels, **kw))
    db.commit()


# ===== 资产提交入口：负责人必填 + 渠道校验 + upsert =====


def test_provision_requires_owner(auth_token, client):
    """新增子机：负责人必填（缺失 → 422）。"""
    r = client.post(
        "/api/v1/assets/provision",
        json={"ip": "10.77.0.1", "password": "x"},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert r.status_code == 422


def test_owner_notify_validation(auth_token, client, db, monkeypatch):
    """渠道校验：至少一项；所选渠道地址必填；webhook 必须 URL。"""
    from app import api as api_mod

    monkeypatch.setattr(api_mod, "_spawn_agent_deploy", lambda aid, **kw: None)
    headers = {"Authorization": f"Bearer {auth_token}"}

    # 渠道为空 → 400
    r = client.post(
        "/api/v1/assets/provision",
        json={"ip": "10.77.0.2", "password": "x", "owner": "赵六", "owner_notify": {"channels": []}},
        headers=headers,
    )
    assert r.status_code == 400
    assert "至少选择一种推送方式" in r.json()["detail"]

    # 选邮箱未填地址 → 400
    r = client.post(
        "/api/v1/assets/provision",
        json={"ip": "10.77.0.3", "password": "x", "owner": "赵六", "owner_notify": {"channels": ["email"]}},
        headers=headers,
    )
    assert r.status_code == 400
    assert "邮箱" in r.json()["detail"]

    # 钉钉地址非 URL → 400
    r = client.post(
        "/api/v1/assets/provision",
        json={
            "ip": "10.77.0.4",
            "password": "x",
            "owner": "赵六",
            "owner_notify": {"channels": ["dingtalk"], "dingtalk_webhook": "not-a-url"},
        },
        headers=headers,
    )
    assert r.status_code == 400
    assert "webhook" in r.json()["detail"]
    assert db.query(Asset).filter(Asset.id == "node-10-77-0-4-22").first() is None  # 校验失败不产生台账


def test_update_upserts_contact_and_lists(auth_token, client, db):
    """编辑资产：owner_notify 按姓名 upsert；联系人列表接口可查（联想回填用）。"""
    db.add(Asset(id="node-edit-1", hostname="node-edit-1", app="", role="app", env="prod",
                 owner="钱七", kind="child", tenant_id="t1", extra={}))
    db.commit()
    headers = {"Authorization": f"Bearer {auth_token}"}
    r = client.patch(
        "/api/v1/assets/node-edit-1",
        json={
            "owner": "钱七",
            "owner_notify": {"channels": ["feishu"], "feishu_webhook": "https://open.feishu.cn/hook/t"},
        },
        headers=headers,
    )
    assert r.status_code == 200
    row = db.query(OwnerContact).filter(OwnerContact.name == "钱七").first()
    assert row is not None and row.channels == ["feishu"]

    items = client.get("/api/v1/assets/owner-contacts", headers=headers).json()["items"]
    assert any(i["name"] == "钱七" and i["channels"] == ["feishu"] for i in items)


# ===== 告警触发 → 渠道推送 =====


def test_alert_cycle_notifies_owner_channels(auth_token, client, db, monkeypatch):
    """子机越限告警：向负责人启用的渠道推送告警信息；短信 stub 留痕；逐渠道记录结果。"""
    _mk_child(db, "ch-ntf", owner="李四", extra={"alert_policy": _policy()})
    _contact(db, "李四", ["sms", "dingtalk"], phone="13900000000",
             dingtalk_webhook="https://oapi.dingtalk.com/robot/send?access_token=t")
    sent = {}

    def _fake_dingtalk(url, title, body):
        sent["url"] = url
        sent["title"] = title
        return True, "sent"

    monkeypatch.setattr(owner_notify, "_send_dingtalk", _fake_dingtalk)

    from app.models import SystemMetricSample

    now = utcnow()
    for sec in (50, 40, 30):
        db.add(SystemMetricSample(asset_id="ch-ntf", ts=now - timedelta(seconds=sec), cpu=80.0))
    db.commit()

    stats = run_alert_cycle(db)
    assert stats.get("owner_notified") == 1
    rows = db.query(Notification).filter(Notification.kind == "owner_alert").all()
    by_channel = {r.channel: r for r in rows}
    assert set(by_channel) == {"owner-sms", "owner-dingtalk"}
    # 短信：供应商未对接 → stub 留痕不算失败
    assert by_channel["owner-sms"].payload["notify"]["ok"] is True
    assert "供应商未对接" in by_channel["owner-sms"].payload["notify"]["reason"]
    # 钉钉：mock 发送成功，URL/标题正确
    assert by_channel["owner-dingtalk"].payload["notify"]["ok"] is True
    assert sent["url"].startswith("https://oapi.dingtalk.com")
    assert "ch-ntf" in sent["title"]
    assert by_channel["owner-dingtalk"].payload["owner"] == "李四"


def test_alert_without_contact_notifies_nothing(auth_token, client, db):
    """未配置联系人/渠道：扫描正常，不产生推送记录（不影响告警主流程）。"""
    _mk_child(db, "ch-non", owner="", extra={"alert_policy": _policy()})
    from app.models import SystemMetricSample

    now = utcnow()
    for sec in (50, 40, 30):
        db.add(SystemMetricSample(asset_id="ch-non", ts=now - timedelta(seconds=sec), cpu=80.0))
    db.commit()
    stats = run_alert_cycle(db)
    assert stats["triggered"] >= 1
    assert "owner_notified" not in stats
    assert db.query(Notification).filter(Notification.kind == "owner_alert").count() == 0


# ===== AI 分析完成 → 推送分析结论 =====


def test_notify_ai_analysis_dispatches_channels(auth_token, client, db, monkeypatch):
    """AI 分析完成：向负责人推送结论（含 severity/结论/建议）；非 done 不推送。"""
    _mk_child(db, "ch-ai", owner="王五")
    _contact(db, "王五", ["wecom", "email"], wecom_webhook="https://qyapi.weixin.qq.com/hook/k",
             email="ww@example.com")
    wecom_bodies = []
    monkeypatch.setattr(owner_notify, "_send_wecom", lambda url, t, b: (wecom_bodies.append(b), (True, "sent"))[1])

    now = utcnow()
    ev = AnomalyEvent(
        event_id="metric-ch-ai-cpu-1", host="ch-ai", hostname="ch-ai", ip="10.0.0.8",
        trigger_name="CPU 使用率", severity="P1", message="CPU 80%>=50%", status="abnormal",
        asset_id="ch-ai", first_seen_at=now, last_seen_at=now,
    )
    db.add(ev)
    db.flush()
    db.add(AiAnalysis(anomaly_id=ev.id, status="done", severity="high", summary="CPU 持续高位",
                      diagnosis="进程 stress 占用过高", solutions=[{"title": "排查压测进程", "detail": ""}],
                      confidence=0.9))
    db.commit()

    rows = owner_notify.notify_ai_analysis(db, ev.id)
    assert {r.channel for r in rows} == {"owner-wecom", "owner-email"}
    body = wecom_bodies[0]
    assert "CPU 持续高位" in body and "排查压测进程" in body

    # 非 done（failed）→ 不推送
    db.add(AiAnalysis(anomaly_id=ev.id, status="failed", error="模型超时"))
    db.commit()
    assert owner_notify.notify_ai_analysis(db, ev.id) == []


# ===== webhook 告警 → 负责人推送（仅首次异常，重复不再推） =====


def test_webhook_first_abnormal_notifies_owner(auth_token, client, db, monkeypatch):
    from tests.conftest import auth_headers

    monkeypatch.setenv("WEBHOOK_AUTO_TICKET", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    sent = []
    monkeypatch.setattr(owner_notify, "_send_dingtalk", lambda url, t, b: (sent.append(b), (True, "sent"))[1])

    _mk_child(db, "demo-host-01", owner="孙八")
    _contact(db, "孙八", ["dingtalk"], dingtalk_webhook="https://oapi.dingtalk.com/robot/send?access_token=z")

    problem = {
        "EVENT.ID": "9101", "EVENT.VALUE": "1", "EVENT.NAME": "High CPU",
        "TRIGGER.NAME": "High CPU", "EVENT.SEVERITY": "High",
        "HOST.NAME": "demo-host-01", "HOST.HOST": "demo-host-01", "HOST.IP": "10.0.0.51",
    }
    r1 = client.post("/api/v1/webhooks/zabbix", json=problem, headers=auth_headers())
    assert r1.status_code == 200
    assert len(sent) == 1 and "demo-host-01" in sent[0]
    assert db.query(Notification).filter(Notification.kind == "owner_alert").count() == 1

    # 重复上报同一异常（duplicate）→ 不重复推送
    r2 = client.post("/api/v1/webhooks/zabbix", json=problem, headers=auth_headers())
    assert r2.status_code == 200
    assert len(sent) == 1

    # 恢复 → 不推送
    resolved = dict(problem, **{"EVENT.VALUE": "0"})
    r3 = client.post("/api/v1/webhooks/zabbix", json=resolved, headers=auth_headers())
    assert r3.status_code == 200
    assert len(sent) == 1


@pytest.mark.parametrize("channels,field,value", [
    (["sms"], "phone", "13900000000"),
    (["email"], "email", "a@b.com"),
])
def test_validate_payload_ok(auth_token, client, channels, field, value):
    """校验通过路径：合法配置返回空错误。"""
    chans, err = owner_notify.validate_notify_payload({"channels": channels, field: value})
    assert err is None and chans == channels
