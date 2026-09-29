from __future__ import annotations

from typing import Iterable

from app.models import TicketStatus

ALLOWED = {
    TicketStatus.pending_analysis: {
        TicketStatus.pending_approval,
        TicketStatus.pending_execution,
        TicketStatus.escalated,
        TicketStatus.skipped,
    },
    TicketStatus.pending_approval: {
        TicketStatus.pending_execution,
        TicketStatus.escalated,
    },
    TicketStatus.pending_execution: {
        TicketStatus.executing,
        TicketStatus.escalated,
    },
    TicketStatus.executing: {
        TicketStatus.verifying,
        TicketStatus.escalated,
    },
    TicketStatus.verifying: {
        TicketStatus.recovered,
        TicketStatus.escalated,
    },
    TicketStatus.recovered: set(),
    TicketStatus.skipped: set(),
    TicketStatus.escalated: set(),
}

TERMINAL = {TicketStatus.recovered, TicketStatus.escalated, TicketStatus.skipped}


class InvalidTransition(Exception):
    def __init__(self, current: TicketStatus, target: TicketStatus):
        super().__init__(f"非法状态迁移: {current.value} → {target.value}")
        self.current = current
        self.target = target


def parse_status(value: str | TicketStatus) -> TicketStatus:
    if isinstance(value, TicketStatus):
        return value
    return TicketStatus(value)


def can_transition(current: str | TicketStatus, target: str | TicketStatus) -> bool:
    cur = parse_status(current)
    nxt = parse_status(target)
    return nxt in ALLOWED.get(cur, set())


def transition(current: str | TicketStatus, target: str | TicketStatus) -> TicketStatus:
    cur = parse_status(current)
    nxt = parse_status(target)
    if nxt not in ALLOWED.get(cur, set()):
        raise InvalidTransition(cur, nxt)
    return nxt


def is_terminal(status: str | TicketStatus) -> bool:
    return parse_status(status) in TERMINAL


def allowed_targets(current: str | TicketStatus) -> Iterable[TicketStatus]:
    return tuple(ALLOWED.get(parse_status(current), set()))
