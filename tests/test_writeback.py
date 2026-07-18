import datetime as dt
from decimal import Decimal

from accrual_agent.engine.writeback import je_external_id
from accrual_agent.models import AccrualStatus, SourceType


def seed(rt, vendor="V-ACME", ref="PO-1001", amount="28500.00", **kw):
    defaults = {
        "vendor_id": vendor, "vendor_name": vendor, "period": "2026-06",
        "source_type": SourceType.NETSUITE_RECEIPT, "source_ref": ref,
        "estimate_basis": "test", "amount": Decimal(amount), "currency": "USD",
        "exchange_rate": Decimal("1"), "gl_account": "6210",
        "cost_center": "CC-ENG", "subsidiary_id": "1",
    }
    defaults.update(kw)
    line, _ = rt.register.upsert_line(**defaults)
    return line


def test_external_id_is_deterministic():
    a = je_external_id("V-ACME", "2026-06", "PO-1001")
    b = je_external_id("V-ACME", "2026-06", "PO-1001")
    c = je_external_id("V-ACME", "2026-07", "PO-1001")
    assert a == b and a != c and a.startswith("ACRJE-")


def test_estimated_lines_never_post(runtime_factory):
    rt = runtime_factory(day=5)
    seed(rt)  # stays ESTIMATED
    period = rt.calendar.period_by_name("2026-06")
    posted, flags = rt.writeback.post_eligible(period, close_day=5)
    assert posted == 0
    assert rt.repo.journal_entries() == []


def test_confirmed_line_posts_with_reversal_and_memo(runtime_factory):
    rt = runtime_factory(day=5)
    line = seed(rt)
    line = rt.register.transition(
        line, AccrualStatus.CONFIRMED, confirmed_amount=Decimal("28500.00"),
        confirmed_source="vendor_reply",
    )
    period = rt.calendar.period_by_name("2026-06")
    posted, flags = rt.writeback.post_eligible(period, close_day=5)
    assert posted == 1 and flags == []
    je = rt.repo.je_for_line(line.line_id)
    assert je.tran_date == dt.date(2026, 6, 30)
    assert je.reversal_date == dt.date(2026, 7, 1)          # auto-reversing JE
    assert je.debit_account == "6210" and je.credit_account == "2150"
    assert "V-ACME" in je.memo and "PO-1001" in je.memo and line.line_id in je.memo
    assert rt.repo.get_line(line.line_id).status == AccrualStatus.POSTED


def test_duplicate_posting_is_impossible(runtime_factory):
    rt = runtime_factory(day=5)
    line = seed(rt)
    rt.register.transition(line, AccrualStatus.CONFIRMED,
                           confirmed_amount=Decimal("28500.00"))
    period = rt.calendar.period_by_name("2026-06")
    first, _ = rt.writeback.post_eligible(period, close_day=5)
    second, _ = rt.writeback.post_eligible(period, close_day=5)
    assert (first, second) == (1, 0)
    assert len(rt.repo.journal_entries()) == 1
    # NetSuite-side dedupe: same external id resolves to the same internal id
    mock_ns = rt.adapters.netsuite
    je = rt.repo.je_for_line(line.line_id)
    assert mock_ns.post_journal_entry(je) == je.netsuite_id


def test_unmapped_gl_blocks_posting(runtime_factory):
    rt = runtime_factory(day=5)
    line = seed(rt, vendor="V-DELTA", ref="PO-1005", gl_account=None)
    rt.register.transition(line, AccrualStatus.CONFIRMED,
                           confirmed_amount=Decimal("28500.00"))
    period = rt.calendar.period_by_name("2026-06")
    posted, flags = rt.writeback.post_eligible(period, close_day=5)
    assert posted == 0
    assert any("no GL mapping" in detail for _, detail in flags)


def test_provisional_api_line_waits_for_settle_window(runtime_factory):
    rt = runtime_factory(day=2)
    line = seed(rt, vendor="V-GOOGLE", ref="1234567890",
                source_type=SourceType.GOOGLE_ADS, comm_suppressed=True,
                provisional=True, gl_account="6510")
    rt.register.transition(
        line, AccrualStatus.AUTO_CONFIRMED,
        confirmed_amount=Decimal("28500.00"), confirmed_source="google_ads",
    )
    period = rt.calendar.period_by_name("2026-06")
    posted, _ = rt.writeback.post_eligible(period, close_day=2)
    assert posted == 0                      # still inside the 72h settle window
    posted, _ = rt.writeback.post_eligible(period, close_day=10)
    assert posted == 1                      # final day forces the latest number


def test_multicurrency_je_carries_rate(runtime_factory):
    rt = runtime_factory(day=5)
    line = seed(rt, vendor="V-ZETA", ref="PO-1003", amount="12000.00",
                currency="EUR", exchange_rate=Decimal("1.09"),
                gl_account="6620", subsidiary_id="2")
    rt.register.transition(line, AccrualStatus.CONFIRMED,
                           confirmed_amount=Decimal("12000.00"))
    period = rt.calendar.period_by_name("2026-06")
    rt.writeback.post_eligible(period, close_day=5)
    je = rt.repo.je_for_line(line.line_id)
    assert je.currency == "EUR"
    assert je.exchange_rate == Decimal("1.09")
    assert je.subsidiary_id == "2"
