"""负责人告警推送：子机出现告警时，按负责人配置的渠道推送告警信息与 AI 日志分析。

渠道：sms（电话短信，供应商对接预留 stub）/ email（SMTP）/ dingtalk / feishu / wecom（机器人 webhook）。
设计要点：
- 联系人按姓名唯一（owner_contacts），资产提交（新增子机/编辑资产）时 upsert；
- 触发点：alert_engine 新告警、webhook 首次异常 → notify_alert；AI 分析完成 → notify_ai_analysis；
- 单渠道失败不影响其他渠道；逐渠道写入 notifications 留痕（channel=owner-{渠道}）；
- 短信渠道：完整走消息生成与渠道路由，sms_provider 未配置时跳过实际发送并记录（供应商环节后补）。
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AiAnalysis, AnomalyEvent, Asset, Notification, OwnerContact, utcnow

log = logging.getLogger("devops.owner_notify")

CHANNELS = ("sms", "email", "dingtalk", "feishu", "wecom")
CHANNEL_LABELS = {
    "sms": "电话短信",
    "email": "邮箱",
    "dingtalk": "钉钉",
    "feishu": "飞书",
    "wecom": "企业微信",
}
# 渠道 → 联系人上的接收地址字段（选了渠道就必须填对应地址）
CHANNEL_FIELD = {
    "sms": "phone",
    "email": "email",
    "dingtalk": "dingtalk_webhook",
    "feishu": "feishu_webhook",
    "wecom": "wecom_webhook",
}
HTTP_TIMEOUT = 5.0


# ===== 联系人 CRUD =====


def get_contact(db: Session, name: str) -> OwnerContact | None:
    return db.scalar(select(OwnerContact).where(OwnerContact.name == name))


def list_contacts(db: Session, q: str = "") -> list[OwnerContact]:
    stmt = select(OwnerContact).order_by(OwnerContact.name.asc()).limit(200)
    if q.strip():
        stmt = stmt.where(OwnerContact.name.like(f"%{q.strip()[:32]}%"))
    return list(db.scalars(stmt))


def upsert_contact(db: Session, name: str, data: dict[str, Any]) -> OwnerContact:
    """按姓名 upsert 联系人（资产提交入口调用）；channels 白名单过滤、去重、保序。"""
    name = (name or "").strip()[:64]
    if not name:
        raise ValueError("负责人姓名不能为空")
    channels = [c for c in (data.get("channels") or []) if c in CHANNELS]
    seen: list[str] = []
    for c in channels:
        if c not in seen:
            seen.append(c)
    row = get_contact(db, name)
    if row is None:
        row = OwnerContact(name=name, channels=seen)
        db.add(row)
    else:
        row.channels = seen
    for f in ("phone", "email", "dingtalk_webhook", "feishu_webhook", "wecom_webhook"):
        setattr(row, f, (data.get(f) or "").strip()[:512])
    row.updated_at = utcnow()
    db.flush()
    return row


def validate_notify_payload(data: dict[str, Any]) -> tuple[list[str], str | None]:
    """校验推送配置：至少选一个渠道，且所选渠道地址必填。返回 (channels, 错误信息)。"""
    channels = [c for c in (data.get("channels") or []) if c in CHANNELS]
    if not channels:
        return [], "请至少选择一种推送方式（电话短信/邮箱/钉钉/飞书/企业微信）"
    for c in channels:
        val = (data.get(CHANNEL_FIELD[c]) or "").strip()
        if not val:
            return channels, f"已选「{CHANNEL_LABELS[c]}」但未填写对应{'接收号码' if c == 'sms' else '地址'}"
        if c in ("dingtalk", "feishu", "wecom") and not val.startswith(("https://", "http://")):
            return channels, f"「{CHANNEL_LABELS[c]}」地址需为完整 webhook URL（https:// 开头）"
        if c == "email" and ("@" not in val or val.startswith("@") or val.endswith("@")):
            return channels, "邮箱地址格式不正确"
    return channels, None


# ===== 触发入口 =====


def notify_alert(db: Session, anomaly_id: int) -> list[Notification]:
    """告警产生：向资产负责人推送告警信息（AI 分析随后完成时由 notify_ai_analysis 单独推送）。"""
    ev = db.get(AnomalyEvent, anomaly_id)
    if ev is None or ev.status != "abnormal":
        return []
    asset = db.get(Asset, ev.asset_id) if ev.asset_id else None
    contact = get_contact(db, asset.owner) if asset is not None and asset.owner else None
    if contact is None or not (contact.channels or []):
        log.info("告警推送跳过 anomaly=%s 无负责人或未配置渠道", anomaly_id)
        return []

    title = f"【新知AI-智能巡检 告警】{ev.hostname or ev.host or ev.ip}"
    body = _alert_text(ev, asset)
    payload = {
        "anomaly_id": ev.id,
        "event_id": ev.event_id,
        "asset_id": ev.asset_id,
        "owner": asset.owner,
        "severity": ev.severity,
    }
    return _dispatch(db, contact, "owner_alert", title, body, payload)


def notify_ai_analysis(db: Session, anomaly_id: int, analysis_id: int | None = None) -> list[Notification]:
    """AI 日志分析完成：推送分析结论（仅 status=done 有内容；其余状态不推送）。"""
    ev = db.get(AnomalyEvent, anomaly_id)
    if ev is None:
        return []
    stmt = select(AiAnalysis).where(AiAnalysis.anomaly_id == anomaly_id).order_by(AiAnalysis.id.desc()).limit(1)
    if analysis_id is not None:
        row = db.get(AiAnalysis, analysis_id)
    else:
        row = db.scalar(stmt)
    if row is None or row.status != "done":
        return []
    asset = db.get(Asset, ev.asset_id) if ev.asset_id else None
    contact = get_contact(db, asset.owner) if asset is not None and asset.owner else None
    if contact is None or not (contact.channels or []):
        return []

    title = f"【AI 日志分析】{ev.hostname or ev.host or ev.ip} 告警分析完成"
    body = _ai_text(ev, row)
    payload = {
        "anomaly_id": ev.id,
        "analysis_id": row.id,
        "asset_id": ev.asset_id,
        "owner": asset.owner if asset is not None else "",
        "ai_status": row.status,
    }
    return _dispatch(db, contact, "owner_ai_analysis", title, body, payload)


# ===== 渠道派发 =====


def _dispatch(
    db: Session, contact: OwnerContact, kind: str, title: str, body: str, payload: dict[str, Any]
) -> list[Notification]:
    """按联系人启用渠道逐个发送；单渠道失败不影响其他；逐渠道留痕。"""
    rows: list[Notification] = []
    senders = {
        "sms": _send_sms,
        "email": _send_email,
        "dingtalk": _send_dingtalk,
        "feishu": _send_feishu,
        "wecom": _send_wecom,
    }
    for channel in [c for c in CHANNELS if c in (contact.channels or [])]:
        address = getattr(contact, CHANNEL_FIELD[channel], "")
        try:
            ok, reason = senders[channel](address, title, body)
        except Exception as exc:  # noqa: BLE001 单渠道异常兜底
            ok, reason = False, f"发送异常: {exc}"
        rows.append(
            _store(
                db,
                kind=kind,
                channel=f"owner-{channel}",
                title=title,
                body=body,
                payload={
                    **payload,
                    "notify": {"owner": contact.name, "channel": channel, "ok": ok, "reason": reason},
                },
            )
        )
        if not ok:
            log.warning("告警推送失败 owner=%s channel=%s reason=%s", contact.name, channel, reason)
    db.flush()
    return rows


def _store(db: Session, *, kind: str, channel: str, title: str, body: str, payload: dict[str, Any]) -> Notification:
    row = Notification(
        ticket_id=None,
        kind=kind,
        channel=channel,
        title=title,
        body=body,
        payload=payload,
        created_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


# ===== 各渠道实现（纯函数：返回 (ok, reason)） =====


def _send_sms(phone: str, title: str, body: str) -> tuple[bool, str]:
    """电话短信渠道：消息生成与渠道路由完整；供应商未对接时跳过实际发送（后续接入 provider 即生效）。"""
    text = f"{title}\n{body}".strip()[:500]
    provider = (get_settings().sms_provider or "").strip()
    if not provider:
        log.info("[SMS stub] 供应商未对接，短信未实际发送 -> %s: %s", phone, text.replace("\n", " | ")[:120])
        return True, "短信供应商未对接，消息已生成待发送（stub）"
    # 预留：按 provider 分发到真实 SDK/HTTP 网关
    return False, f"未知短信供应商: {provider}"


def _send_email(to: str, title: str, body: str) -> tuple[bool, str]:
    s = get_settings()
    if not (s.smtp_host and s.smtp_sender):
        return False, "SMTP 未配置（需设置 SMTP_HOST/SMTP_SENDER）"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = formataddr((str(Header("新知AI-智能巡检", "utf-8")), s.smtp_sender))
    msg["To"] = to
    if s.smtp_use_ssl:
        server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=HTTP_TIMEOUT)
    else:
        server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=HTTP_TIMEOUT)
    try:
        if s.smtp_user:
            server.login(s.smtp_user, s.smtp_password)
        server.sendmail(s.smtp_sender, [to], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass
    return True, "sent"


def _post_webhook(url: str, data: dict) -> tuple[bool, str]:
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        resp = client.post(url, json=data)
    if resp.status_code == 200:
        return True, "sent"
    return False, f"HTTP {resp.status_code}: {resp.text[:120]}"


def _send_dingtalk(url: str, title: str, body: str) -> tuple[bool, str]:
    return _post_webhook(url, {"msgtype": "markdown", "markdown": {"title": title, "text": f"### {title}\n\n{body}"}})


def _send_feishu(url: str, title: str, body: str) -> tuple[bool, str]:
    return _post_webhook(url, {"msg_type": "text", "content": {"text": f"{title}\n{body}"}})


def _send_wecom(url: str, title: str, body: str) -> tuple[bool, str]:
    content = f"{title}\n{body}".strip()
    if len(content) > 3800:
        content = content[:3800] + "…"
    return _post_webhook(url, {"msgtype": "markdown", "markdown": {"content": content}})


# ===== 消息文案 =====


def _alert_text(ev: AnomalyEvent, asset: Asset | None) -> str:
    lines = [
        f"级别：{ev.severity or 'high'}",
        f"主机：{ev.hostname or ev.host or '-'}（{ev.ip or '-'}）",
        f"告警：{ev.trigger_name or ev.message or '-'}",
        f"负责人：{asset.owner if asset is not None else '-'}",
    ]
    p = ev.payload or {}
    for k, label in (("value", "当前值"), ("threshold", "阈值"), ("rule_key", "规则")):
        if p.get(k) not in (None, ""):
            lines.append(f"{label}：{p[k]}")
    if ev.first_seen_at is not None:
        lines.append(f"时间：{ev.first_seen_at.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    lines.append("AI 日志分析完成后将另行推送。")
    return "\n".join(lines)


def _ai_text(ev: AnomalyEvent, row: AiAnalysis) -> str:
    lines = [
        f"主机：{ev.hostname or ev.host or '-'}（{ev.ip or '-'}）",
        f"告警：{ev.trigger_name or ev.message or '-'}",
        f"严重程度：{row.severity or ev.severity or '-'}（置信度 {round((row.confidence or 0) * 100)}%）",
        f"结论：{row.summary or '-'}",
    ]
    if row.diagnosis:
        diag = row.diagnosis.strip()
        lines.append(f"诊断：{diag[:400]}{'…' if len(diag) > 400 else ''}")
    solutions = [s for s in (row.solutions or []) if isinstance(s, dict) and s.get("title")]
    if solutions:
        lines.append("建议：")
        lines.extend(f"{i}. {s['title']}" for i, s in enumerate(solutions[:3], 1))
    return "\n".join(lines)
