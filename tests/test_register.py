from decimal import Decimal

import pytest

from accrual_agent.models import AccrualStatus, SourceType
from accrual_agent.register.repository import Repository
from accrual_agent.register.service import IllegalTransitionError, RegisterService


@pytest.fixture
def register(tmp_path) -> RegisterService:
    return RegisterService(Repository(tmp_path / "reg.db"))


def make_line(register: RegisterService, **overrides):
    defaults = {
        "vendor_id": "V-TEST",
        "vendor_name": "Test Vendor",
        "period": "2026-06",
        "source_type": SourceType.NETSUITE_RECEIPT,
        "source_ref": "PO-9",
        "estimate_basis": "test",
        "amount": Decimal("1000.00"),
        "currency": "USD",
        "exchange_rate": Decimal("1"),
        "gl_account": "6000",
        "cost_center": "CC-X",
        "subsidiary_id": "1",
    }
    defaults.update(overrides)
    line, created = register.upsert_line(**defaults)
    return line, created


def test_create_and_natural_key_dedupe(register):
    line, created = make_line(register)
    assert created and line.line_id == "ACR-2026-06-0001"
    assert line.ref_token == "[ACR-2026-06-0001]"
    assert line.status == AccrualStatus.ESTIMATED
    same, created2 = make_line(register)
    assert not created2 and same.line_id == line.line_id


def test_estimate_refresh_only_while_unconfirmed(register):
    line, _ = make_line(register)
    refreshed, _ = make_line(register, amount=Decimal("1200.00"))
    assert refreshed.amount == Decimal("1200.00")
    assert refreshed.base_amount == Decimal("1200.00")

    register.transition(refreshed, AccrualStatus.CONFIRMED,
                        confirmed_amount=Decimal("1200.00"))
    stale, _ = make_line(register, amount=Decimal("999.00"))
    assert stale.amount == Decimal("1200.00")  # confirmed amounts never silently move


def test_estimated_line_can_never_post(register):
    line, _ = make_line(register)
    with pytest.raises(IllegalTransitionError):
        register.transition(line, AccrualStatus.POSTED)


def test_held_line_requires_approval_before_posting(register):
    line, _ = make_line(register)
    held = register.transition(line, AccrualStatus.HELD_FOR_REVIEW,
                               hold_reason="variance")
    with pytest.raises(IllegalTransitionError):
        register.transition(held, AccrualStatus.POSTED)
    approved = register.transition(held, AccrualStatus.CONFIRMED, actor="controller")
    posted = register.transition(approved, AccrualStatus.POSTED)
    assert posted.status == AccrualStatus.POSTED


def test_cleared_is_terminal(register):
    line, _ = make_line(register)
    line = register.transition(line, AccrualStatus.CONFIRMED)
    line = register.transition(line, AccrualStatus.POSTED)
    line = register.transition(line, AccrualStatus.CLEARED)
    with pytest.raises(IllegalTransitionError):
        register.transition(line, AccrualStatus.CONFIRMED)


def test_every_change_is_audited(register):
    line, _ = make_line(register)
    register.transition(line, AccrualStatus.CONFIRMED,
                        actor="alice@yourco.example", source="review",
                        confirmed_amount=Decimal("1000.00"))
    rows = register.repo.audit_rows(line.line_id)
    fields = {r["field"] for r in rows}
    assert "status" in fields and "confirmed_amount" in fields
    actors = {r["actor"] for r in rows}
    assert "alice@yourco.example" in actors


def test_line_ids_are_sequential_per_period(register):
    a, _ = make_line(register, source_ref="PO-1")
    b, _ = make_line(register, source_ref="PO-2")
    c, _ = make_line(register, source_ref="PO-3", period="2026-07")
    assert a.line_id.endswith("0001") and b.line_id.endswith("0002")
    assert c.line_id == "ACR-2026-07-0001"
