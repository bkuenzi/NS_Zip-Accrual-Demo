from decimal import Decimal

import pytest

from accrual_agent.comms.cadence import Cadence
from accrual_agent.config import GLMappingStore
from accrual_agent.engine.confirmation import ConfirmationService, variance_pct
from accrual_agent.models import (
    AccrualStatus,
    CommStage,
    EscalationReason,
    ParsedVendorReply,
    SourceType,
)
from accrual_agent.register.repository import Repository
from accrual_agent.register.service import RegisterService


@pytest.fixture
def register(tmp_path):
    return RegisterService(Repository(tmp_path / "reg.db"))


@pytest.fixture
def confirmation(settings, register):
    return ConfirmationService(settings, register, GLMappingStore.load())


def seed_line(register, amount="10000.00", vendor="V-BETA", gl="6410"):
    line, _ = register.upsert_line(
        vendor_id=vendor, vendor_name=vendor, period="2026-06",
        source_type=SourceType.NETSUITE_RECEIPT, source_ref="PO-77",
        estimate_basis="test", amount=Decimal(amount), currency="USD",
        exchange_rate=Decimal("1"), gl_account=gl, cost_center="CC",
        subsidiary_id="1",
    )
    return line


def reply(amount, confidence=0.9, currency="USD", **kwargs):
    return ParsedVendorReply(
        confirmed_amount=Decimal(amount) if amount else None,
        currency=currency, confidence=confidence, **kwargs,
    )


def test_within_threshold_accepts_vendor_amount(register, confirmation):
    line = seed_line(register)
    flags = confirmation.apply_reply(line, reply("10200.00"))  # 2% < 5%
    assert flags == []
    updated = register.repo.get_line(line.line_id)
    assert updated.status == AccrualStatus.CONFIRMED
    assert updated.confirmed_amount == Decimal("10200.00")


def test_beyond_threshold_holds_for_review(register, confirmation):
    line = seed_line(register)
    flags = confirmation.apply_reply(line, reply("11500.00"))  # 15% > 5%
    assert flags and flags[0][0] == EscalationReason.VARIANCE_BREACH
    updated = register.repo.get_line(line.line_id)
    assert updated.status == AccrualStatus.HELD_FOR_REVIEW
    assert "15.00%" in (updated.hold_reason or "")


def test_per_gl_threshold_override_applies(register, confirmation):
    # GL 6620 carries a ±10% override in config/gl_mappings.yaml
    line = seed_line(register, vendor="V-GAMMA", gl="6620")
    flags = confirmation.apply_reply(line, reply("10800.00"))  # 8% < 10%
    assert flags == []
    assert register.repo.get_line(line.line_id).status == AccrualStatus.CONFIRMED


def test_unparseable_reply_never_confirms(register, confirmation):
    line = seed_line(register)
    flags = confirmation.apply_reply(line, reply(None, confidence=0.1))
    assert flags[0][0] == EscalationReason.UNPARSEABLE_REPLY
    assert register.repo.get_line(line.line_id).status == AccrualStatus.ESTIMATED


def test_currency_mismatch_holds(register, confirmation):
    line = seed_line(register)
    flags = confirmation.apply_reply(line, reply("10000.00", currency="EUR"))
    assert flags[0][0] == EscalationReason.VARIANCE_BREACH
    assert register.repo.get_line(line.line_id).status == AccrualStatus.HELD_FOR_REVIEW


def test_invoice_confirmation_uses_same_gate(register, confirmation):
    line = seed_line(register)
    flags = confirmation.apply_invoice(line, "INV-1", Decimal("10100.00"))
    assert flags == []
    updated = register.repo.get_line(line.line_id)
    assert updated.status == AccrualStatus.CONFIRMED
    assert updated.invoice_number == "INV-1"
    assert updated.confirmed_source == "netsuite_bill"


def test_variance_pct():
    assert variance_pct(Decimal("100"), Decimal("105")) == Decimal("5.00")
    assert variance_pct(Decimal("0"), Decimal("1")) == Decimal("100")


# ── cadence ──────────────────────────────────────────────────────────────────


def test_cadence_due_stages_escalate_over_close_days():
    cadence = Cadence([3, 7, 10], final_close_day=10)
    assert cadence.due_stages(0) == []
    assert cadence.due_stages(1) == [CommStage.INITIAL]
    assert cadence.due_stages(3) == [CommStage.INITIAL, CommStage.DAY3]
    assert cadence.due_stages(10) == [
        CommStage.INITIAL, CommStage.DAY3, CommStage.DAY7, CommStage.DAY10
    ]


def test_cadence_sends_one_stage_per_run_and_never_repeats():
    cadence = Cadence([3, 7, 10], final_close_day=10)
    sent: set[str] = set()
    assert cadence.next_unsent_stage(4, sent) == CommStage.INITIAL
    sent.add("initial")
    assert cadence.next_unsent_stage(4, sent) == CommStage.DAY3
    sent.add("day3")
    assert cadence.next_unsent_stage(4, sent) is None  # day7 not due yet
    sent.update({"day7", "day10"})
    assert cadence.next_unsent_stage(10, sent) is None
    assert cadence.ladder_exhausted(10, sent)


def test_close_risk_window():
    cadence = Cadence([3, 7, 10], final_close_day=10)
    assert not cadence.is_close_risk(7)
    assert cadence.is_close_risk(8)


def test_custom_cadence_days_are_respected():
    cadence = Cadence([2, 4, 6], final_close_day=8)
    assert CommStage.DAY3 in cadence.due_stages(2)
    assert cadence.ladder_exhausted(6, {"initial", "day3", "day7", "day10"})


def test_cadence_requires_three_reminder_days():
    with pytest.raises(ValueError):
        Cadence([3, 7], final_close_day=10)
