from decimal import Decimal

import pytest

from accrual_agent.config import ContactRecord, VendorContactStore
from accrual_agent.models import SourceType, ThreadStatus


@pytest.fixture
def store(tmp_path) -> VendorContactStore:
    return VendorContactStore(path=tmp_path / "contacts.yaml")


def test_no_contact_blocks(store):
    contact, reason = store.verified_contact("V-X", ["x.example"])
    assert contact is None and "no contact" in reason


def test_unverified_contact_blocks(store):
    store.contacts["V-X"] = ContactRecord("V-X", "Pat", "ap@x.example", verified=False)
    contact, reason = store.verified_contact("V-X", ["x.example"])
    assert contact is None and "not marked verified" in reason


def test_domain_mismatch_blocks_even_when_verified(store):
    store.contacts["V-X"] = ContactRecord("V-X", "Pat", "ap@evil.example", verified=True)
    contact, reason = store.verified_contact("V-X", ["x.example"])
    assert contact is None and "does not match" in reason


def test_verified_and_domain_matched_sends(store):
    store.contacts["V-X"] = ContactRecord("V-X", "Pat", "ap@x.example", verified=True)
    contact, reason = store.verified_contact("V-X", ["x.example"])
    assert contact is not None and reason is None


def test_allowlisted_domain_passes(store):
    store.contacts["V-X"] = ContactRecord("V-X", "Pat", "billing@agency.example", verified=True)
    store.allowed_domains["V-X"] = ["agency.example"]
    contact, reason = store.verified_contact("V-X", ["x.example"])
    assert contact is not None


def test_blocked_line_is_flagged_and_nothing_sends(runtime_factory):
    """End-to-end: Epsilon has no contact on file -> blocked, flagged, no mail."""
    rt = runtime_factory(day=1)
    line, _ = rt.register.upsert_line(
        vendor_id="V-EPSILON", vendor_name="Epsilon Facilities Group",
        period="2026-06", source_type=SourceType.NETSUITE_RECEIPT,
        source_ref="PO-1006", estimate_basis="test",
        amount=Decimal("5200.00"), currency="USD", exchange_rate=Decimal("1"),
        gl_account="6710", cost_center="CC-OPS", subsidiary_id="1",
    )
    sent, _ = rt.outbound.process(close_day=1)
    assert sent == 0
    updated = rt.repo.get_line(line.line_id)
    assert updated.thread_status == ThreadStatus.BLOCKED_NO_CONTACT
    assert rt.repo.sent_stages(line.line_id) == set()
    assert all(m.to != "" for m in rt.mailer.sent)  # nothing recorded for Epsilon
    assert len(rt.mailer.sent) == 0


def test_reminders_never_double_send(runtime_factory):
    rt = runtime_factory(day=3)
    rt.register.upsert_line(
        vendor_id="V-ACME", vendor_name="Acme Cloud Services",
        period="2026-06", source_type=SourceType.NETSUITE_RECEIPT,
        source_ref="PO-1001", estimate_basis="test",
        amount=Decimal("28500.00"), currency="USD", exchange_rate=Decimal("1"),
        gl_account="6210", cost_center="CC-ENG", subsidiary_id="1",
    )
    first, _ = rt.outbound.process(close_day=3)   # sends initial
    second, _ = rt.outbound.process(close_day=3)  # sends day3 reminder
    third, _ = rt.outbound.process(close_day=3)   # nothing left due
    assert (first, second, third) == (1, 1, 0)
    line = rt.repo.lines()[0]
    assert rt.repo.sent_stages(line.line_id) == {"initial", "day3"}
