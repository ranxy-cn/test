from app.models import TicketStatus
from app.domain.state_machine import InvalidTransition, can_transition, is_terminal, transition


def test_happy_path_transitions():
    status = TicketStatus.pending_analysis
    status = transition(status, TicketStatus.pending_execution)
    status = transition(status, TicketStatus.executing)
    status = transition(status, TicketStatus.verifying)
    status = transition(status, TicketStatus.recovered)
    assert is_terminal(status)


def test_approval_branch():
    status = transition(TicketStatus.pending_analysis, TicketStatus.pending_approval)
    status = transition(status, TicketStatus.pending_execution)
    assert status is TicketStatus.pending_execution


def test_escalate_from_analysis():
    assert can_transition(TicketStatus.pending_analysis, TicketStatus.escalated)
    assert transition(TicketStatus.verifying, TicketStatus.escalated) is TicketStatus.escalated


def test_reject_illegal_jump():
    try:
        transition(TicketStatus.pending_analysis, TicketStatus.executing)
        assert False, "should raise"
    except InvalidTransition:
        pass


def test_terminal_has_no_outbound():
    assert not can_transition(TicketStatus.recovered, TicketStatus.executing)
    assert not can_transition(TicketStatus.escalated, TicketStatus.recovered)
