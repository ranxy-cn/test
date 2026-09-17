from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Notification, Ticket, utcnow

log = logging.getLogger("devops.notify")

KIND_TITLES = {
    "ticket_created": "任务立案",
    "pending_approval": "等待审批",
    "recovered": "已恢复",
    "escalated": "已升级",
    "lock_queued": "资源锁排队",
}


@runtime_checkable
class Notifier(Protocol):
    """通知通道：立案 / 待审批 / 已恢复 / 已升级。业务代码只依赖本协议。"""

    channel: str

    def send(
        self,
        db: Session,
        *,
        ticket_id: int | None,
        kind: str,
        title: str,
        body: str,
        payload: dict[str, Any],
    ) -> Notification | None: ...


class InboxNotifier:
    channel = "inbox"

    def send(self, db, *, ticket_id, kind, title, body, payload) -> Notification:
        return _store(db, ticket_id, kind, self.channel, title, body, payload)


class LogNotifier:
    channel = "log"

    def send(self, db, *, ticket_id, kind, title, body, payload) -> Notification:
        log.info("notify %s ticket=%s %s", kind, payload.get("number"), body)
        return _store(db, ticket_id, kind, self.channel, title, body, payload)


class WebhookNotifier:
    channel = "webhook"

    def __init__(self, url: str):
        self.url = url

    def send(self, db, *, ticket_id, kind, title, body, payload) -> Notification:
        try:
            with httpx.Client(timeout=5.0) as client:
                client.post(self.url, json={"title": title, "body": body, "payload": payload})
            return _store(db, ticket_id, kind, self.channel, title, body, {"url": self.url, **payload})
        except Exception as exc:  # noqa: BLE001
            log.warning("notify webhook failed: %s", exc)
            return _store(db, ticket_id, kind, self.channel, title, f"webhook 失败: {exc}", payload)


def build_notifiers() -> list[Notifier]:
    """工厂：始终注入日志 + 工作台收件箱；Webhook 仅在配置了 URL 时启用。"""
    items: list[Notifier] = [InboxNotifier(), LogNotifier()]
    url = (get_settings().notify_webhook_url or "").strip()
    if url:
        items.append(WebhookNotifier(url))
    return items


def notify_ticket(db: Session, ticket: Ticket, kind: str, extra: str = "") -> list[Notification]:
    title = KIND_TITLES.get(kind, kind)
    body = extra or f"{ticket.number} {ticket.title} → {kind}"
    payload = {
        "ticket_id": ticket.id,
        "number": ticket.number,
        "asset_id": ticket.asset_id,
        "status": ticket.status,
        "kind": kind,
    }
    rows: list[Notification] = []
    for notifier in build_notifiers():
        row = notifier.send(
            db, ticket_id=ticket.id, kind=kind, title=title, body=body, payload=payload
        )
        if row is not None:
            rows.append(row)
    return rows


def _store(
    db: Session,
    ticket_id: int | None,
    kind: str,
    channel: str,
    title: str,
    body: str,
    payload: dict[str, Any],
) -> Notification:
    row = Notification(
        ticket_id=ticket_id,
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


def list_notifications(db: Session, unread_only: bool = False, ticket_id: int | None = None) -> list[Notification]:
    stmt = select(Notification)
    if unread_only:
        stmt = stmt.where(Notification.read.is_(False))
    if ticket_id is not None:
        stmt = stmt.where(Notification.ticket_id == ticket_id)
        stmt = stmt.order_by(Notification.id.asc())
    else:
        stmt = stmt.order_by(Notification.id.desc()).limit(200)
    return db.scalars(stmt).all()


def mark_read(db: Session, notif_id: int) -> Notification | None:
    row = db.get(Notification, notif_id)
    if row is None:
        return None
    row.read = True
    db.flush()
    return row
