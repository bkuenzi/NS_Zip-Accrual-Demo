"""Interview-driven v2 behaviors: templated replies, LLM second-pass
verification, internal-owner routing, sub-floor aggregation, trust ladder."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from accrual_agent.comms.inbound import parse_reply_template
from accrual_agent.config import GLMappingStore, InternalOwner
from accrual_agent.engine.identification import IdentificationService
from accrual_agent.engine.trust import TrustLadderService
from accrual_agent.integrations.factory import AdapterSet
from accrual_agent.integrations.netsuite.mock import MockNetSuite
from accrual_agent.models import (
    AccrualStatus,
    EscalationReason,
    ParsedVendorReply,
    SourceType,
    ZipRequisition,
)

# ── templated reply parsing ──────────────────────────────────────────────────


FILLED_TEMPLATE = """Hi team,

Happy to help — here you go:

    AMOUNT: $19,850.00
    CURRENCY: USD
    DELIVERED PERCENT (services only — share of the work delivered): 33
    INVOICE NUMBER (if issued): INV-4410
    EXPECTED INVOICE DATE (YYYY-MM-DD): 2026-07-15

Best,
Dana
"""


def test_template_block_parses_deterministically():
    parsed = parse_reply_template(FILLED_TEMPLATE)
    assert parsed is not None
    assert parsed.method == "template"
    assert parsed.confirmed_amount == Decimal("19850.00")
    assert parsed.currency == "USD"
    assert parsed.delivered_pct == Decimal("33")
    assert parsed.invoice_number == "INV-4410"
    assert parsed.expected_invoice_date == dt.date(2026, 7, 15)
    assert parsed.confidence >= 0.9


def test_quoted_unfilled_template_is_not_a_parse():
    # A reply quoting our outbound email leaves AMOUNT blank — no false parse.
    quoted = (
        "Let me check and get back to you.\n\n"
        "> AMOUNT:\n> CURRENCY: USD\n"
        "> DELIVERED PERCENT (services only — share of the work delivered):\n"
    )
    assert parse_reply_template(quoted) is None


def test_template_parse_wins_over_heuristics(runtime_factory):
    rt = runtime_factory(day=3)
    parsed = rt.inbound._parse(_msg(FILLED_TEMPLATE), [])
    assert parsed.method == "template"


def _msg(body: str):
    from accrual_agent.comms.mailer import InboundEmail

    return InboundEmail(
        message_id="<t@test>", sender="a@b.example", subject="Re: x", body=body
    )


# ── vendor-stated delivery basis ─────────────────────────────────────────────


def test_delivered_pct_replaces_proration_basis(runtime_factory):
    rt = runtime_factory(day=3)
    line, _ = rt.register.upsert_line(
        vendor_id="V-ETA", vendor_name="Eta Media Production", period="2026-06",
        source_type=SourceType.NETSUITE_PO, source_ref="PO-1004",
        estimate_basis="prorated 30/92 days of 60,000.00 service PO",
        amount=Decimal("19565.22"), currency="USD", exchange_rate=Decimal("1"),
        gl_account="6520", cost_center="CC-MKT", subsidiary_id="1",
    )
    parsed = ParsedVendorReply(
        confirmed_amount=Decimal("19850.00"), currency="USD",
        delivered_pct=Decimal("33"), confidence=0.95, method="template",
    )
    flags = rt.confirmation.apply_reply(line, parsed)
    assert flags == []
    updated = rt.repo.get_line(line.line_id)
    assert updated.status == AccrualStatus.CONFIRMED
    assert "respondent-stated 33%" in updated.estimate_basis


# ── LLM second-pass verification ─────────────────────────────────────────────


def test_unverified_llm_extraction_is_held(runtime_factory):
    rt = runtime_factory(day=5)
    line, _ = rt.register.upsert_line(
        vendor_id="V-BETA", vendor_name="Beta Logistics", period="2026-06",
        source_type=SourceType.NETSUITE_RECEIPT, source_ref="PO-1002",
        estimate_basis="test", amount=Decimal("18750.00"), currency="USD",
        exchange_rate=Decimal("1"), gl_account="6410", cost_center="CC-OPS",
        subsidiary_id="1",
    )
    parsed = ParsedVendorReply(
        confirmed_amount=Decimal("18800.00"), currency="USD",
        confidence=0.9, method="llm", verified=False,
    )
    flags = rt.confirmation.apply_reply(line, parsed)
    assert flags[0][0] == EscalationReason.UNVERIFIED_EXTRACTION
    updated = rt.repo.get_line(line.line_id)
    assert updated.status == AccrualStatus.HELD_FOR_REVIEW
    assert "second-pass verification" in (updated.hold_reason or "")


def test_verified_llm_extraction_confirms(runtime_factory):
    rt = runtime_factory(day=5)
    line, _ = rt.register.upsert_line(
        vendor_id="V-BETA", vendor_name="Beta Logistics", period="2026-06",
        source_type=SourceType.NETSUITE_RECEIPT, source_ref="PO-1002",
        estimate_basis="test", amount=Decimal("18750.00"), currency="USD",
        exchange_rate=Decimal("1"), gl_account="6410", cost_center="CC-OPS",
        subsidiary_id="1",
    )
    parsed = ParsedVendorReply(
        confirmed_amount=Decimal("18800.00"), currency="USD",
        confidence=0.9, method="llm", verified=True,
    )
    assert rt.confirmation.apply_reply(line, parsed) == []
    updated = rt.repo.get_line(line.line_id)
    assert updated.status == AccrualStatus.CONFIRMED
    assert "independently verified" in (updated.notes or "")


def test_mock_extractor_second_pass_agrees_with_itself():
    from accrual_agent.comms.llm_extractor import MockLLMExtractor

    body = "hiermit bestaetigen wir EUR 12.000,00 fuer Juni. Rechnung RE-2211 folgt."
    extractor = MockLLMExtractor()
    parsed = extractor.extract(body)
    assert parsed is not None
    assert extractor.verify(body, parsed) is True
    disagreeing = parsed.model_copy(update={"confirmed_amount": Decimal("99999")})
    assert extractor.verify(body, disagreeing) is False


# ── internal-owner routing ───────────────────────────────────────────────────


def test_internal_route_sends_to_budget_owner(runtime_factory):
    # V-GAMMA routes internal in config/gl_mappings.yaml (owner on company domain)
    rt = runtime_factory(day=1)
    rt.register.upsert_line(
        vendor_id="V-GAMMA", vendor_name="Gamma Consulting", period="2026-06",
        source_type=SourceType.ZIP_REQUISITION, source_ref="ZR-2088",
        estimate_basis="test", amount=Decimal("15000.00"), currency="USD",
        exchange_rate=Decimal("1"), gl_account="6620", cost_center="CC-FIN",
        subsidiary_id="1",
    )
    sent, _ = rt.outbound.process(close_day=1)
    assert sent == 1
    message = rt.mailer.sent[0]
    assert message.to == "morgan.patel@yourco.example"
    assert "budget owner" in message.body
    assert "Nothing goes to the vendor" in message.body


def test_internal_owner_off_company_domain_blocks(runtime_factory):
    rt = runtime_factory(day=1)
    rt.gl_store.confirmation_overrides["V-ACME"] = "internal"
    rt.gl_store.internal_owners["V-ACME"] = InternalOwner(
        name="Evil Twin", email="owner@evil.example"
    )
    line, _ = rt.register.upsert_line(
        vendor_id="V-ACME", vendor_name="Acme Cloud Services", period="2026-06",
        source_type=SourceType.NETSUITE_RECEIPT, source_ref="PO-1001",
        estimate_basis="test", amount=Decimal("28500.00"), currency="USD",
        exchange_rate=Decimal("1"), gl_account="6210", cost_center="CC-ENG",
        subsidiary_id="1",
    )
    sent, _ = rt.outbound.process(close_day=1)
    assert sent == 0
    updated = rt.repo.get_line(line.line_id)
    assert "not the company domain" in (updated.notes or "")


def test_internal_reply_labeled_as_such(runtime_factory):
    rt = runtime_factory(day=3)
    line, _ = rt.register.upsert_line(
        vendor_id="V-GAMMA", vendor_name="Gamma Consulting", period="2026-06",
        source_type=SourceType.ZIP_REQUISITION, source_ref="ZR-2088",
        estimate_basis="test", amount=Decimal("15000.00"), currency="USD",
        exchange_rate=Decimal("1"), gl_account="6620", cost_center="CC-FIN",
        subsidiary_id="1",
    )
    parsed = ParsedVendorReply(
        confirmed_amount=Decimal("15200.00"), currency="USD", confidence=0.9
    )
    rt.confirmation.apply_reply(line, parsed)
    assert rt.repo.get_line(line.line_id).confirmed_source == "internal_reply"


# ── sub-floor aggregation ────────────────────────────────────────────────────


class _SmallGapsZip:
    def __init__(self, count: int, each: str):
        self.reqs = [
            ZipRequisition(
                requisition_id=f"ZR-S{i}", vendor_id=f"V-SMALL{i}",
                vendor_name=f"Small Vendor {i}", business_unit="BU-US",
                committed_amount=Decimal(each),
                approved_date=dt.date(2026, 6, 10),
                service_start=dt.date(2026, 6, 1), service_end=dt.date(2026, 6, 30),
                gl_account="6800", cost_center="CC-OPS",
            )
            for i in range(count)
        ]

    def get_approved_requisitions(self, start, end):
        return list(self.reqs)


def _bare_netsuite() -> MockNetSuite:
    ns = MockNetSuite()
    ns.purchase_orders, ns.receipts, ns.bills = [], [], []
    return ns


def _identify(settings, register, zip_adapter):
    rt_adapters = AdapterSet(netsuite=_bare_netsuite(), zip=zip_adapter)
    service = IdentificationService(
        settings, rt_adapters, register, GLMappingStore.load()
    )
    from accrual_agent.fiscal import FiscalCalendar

    period = FiscalCalendar.load(timezone=settings.close_timezone).period_by_name(
        "2026-06"
    )
    return service.run(period)


def test_subfloor_gaps_aggregate_into_sundry_line(runtime_factory, settings):
    rt = runtime_factory(day=1)
    _identify(settings, rt.register, _SmallGapsZip(count=6, each="200.00"))  # $1,200
    sundry = [
        ln for ln in rt.repo.lines()
        if ln.source_type == SourceType.SUNDRY_AGGREGATE
    ]
    assert len(sundry) == 1
    line = sundry[0]
    assert line.amount == Decimal("1200.00")
    assert line.comm_suppressed          # never emailed
    assert line.status == AccrualStatus.ESTIMATED   # a human must approve it
    assert line.gl_account == "6890"
    assert "6 gaps below" in line.estimate_basis


def test_subfloor_total_under_threshold_stays_logged_only(runtime_factory, settings):
    rt = runtime_factory(day=1)
    _identify(settings, rt.register, _SmallGapsZip(count=3, each="200.00"))  # $600
    assert all(
        ln.source_type != SourceType.SUNDRY_AGGREGATE for ln in rt.repo.lines()
    )


# ── trust ladder ─────────────────────────────────────────────────────────────


def _seed_cleared_history(register, vendor: str, periods: list[str], variance_ok=True):
    for i, period in enumerate(periods):
        estimate = Decimal("10000.00")
        invoice = estimate if variance_ok else estimate * Decimal("1.20")
        line, _ = register.upsert_line(
            vendor_id=vendor, vendor_name=vendor, period=period,
            source_type=SourceType.NETSUITE_RECEIPT, source_ref=f"PO-H{i}",
            estimate_basis="history", amount=estimate, currency="USD",
            exchange_rate=Decimal("1"), gl_account="6410", cost_center="CC",
            subsidiary_id="1",
        )
        line = register.transition(line, AccrualStatus.CONFIRMED, source="test")
        line = register.transition(line, AccrualStatus.POSTED, source="test")
        register.transition(
            line, AccrualStatus.CLEARED, source="test",
            cleared_invoice_amount=invoice,
        )


@pytest.fixture
def trust(settings, runtime_factory):
    rt = runtime_factory(day=10)
    return rt, TrustLadderService(settings, rt.register, GLMappingStore.load())


def test_accuracy_streak_earns_eligibility(trust):
    rt, service = trust
    _seed_cleared_history(rt.register, "V-BETA", ["2026-03", "2026-04", "2026-05"])
    streaks = {s.vendor_id: s for s in service.streaks()}
    assert streaks["V-BETA"].streak == 3
    assert streaks["V-BETA"].eligible


def test_variance_miss_resets_streak(trust):
    rt, service = trust
    _seed_cleared_history(rt.register, "V-BETA", ["2026-03", "2026-04"])
    _seed_cleared_history(rt.register, "V-BETA", ["2026-05"], variance_ok=False)
    streaks = {s.vendor_id: s for s in service.streaks()}
    assert streaks["V-BETA"].streak == 0
    assert not streaks["V-BETA"].eligible


def test_revoked_vendor_never_promotes(trust):
    rt, service = trust
    _seed_cleared_history(rt.register, "V-BETA", ["2026-03", "2026-04", "2026-05"])
    service.gl_store.trust_revoked.add("V-BETA")
    assert service.eligible_vendor_ids() == set()


def test_promote_auto_confirms_eligible_estimates_only(trust):
    rt, service = trust
    _seed_cleared_history(rt.register, "V-BETA", ["2026-03", "2026-04", "2026-05"])
    eligible_line, _ = rt.register.upsert_line(
        vendor_id="V-BETA", vendor_name="Beta Logistics", period="2026-06",
        source_type=SourceType.NETSUITE_RECEIPT, source_ref="PO-1002",
        estimate_basis="test", amount=Decimal("18750.00"), currency="USD",
        exchange_rate=Decimal("1"), gl_account="6410", cost_center="CC-OPS",
        subsidiary_id="1",
    )
    other_line, _ = rt.register.upsert_line(
        vendor_id="V-EPSILON", vendor_name="Epsilon Facilities Group",
        period="2026-06", source_type=SourceType.NETSUITE_RECEIPT,
        source_ref="PO-1006", estimate_basis="test",
        amount=Decimal("5200.00"), currency="USD", exchange_rate=Decimal("1"),
        gl_account="6710", cost_center="CC-OPS", subsidiary_id="1",
    )
    assert service.promote("2026-06") == 1
    promoted = rt.repo.get_line(eligible_line.line_id)
    assert promoted.status == AccrualStatus.CONFIRMED
    assert promoted.confirmed_source == "trust_ladder"
    assert rt.repo.get_line(other_line.line_id).status == AccrualStatus.ESTIMATED


def test_trust_ladder_post_is_labeled_estimate_based(trust):
    rt, service = trust
    _seed_cleared_history(rt.register, "V-BETA", ["2026-03", "2026-04", "2026-05"])
    rt.register.upsert_line(
        vendor_id="V-BETA", vendor_name="Beta Logistics", period="2026-06",
        source_type=SourceType.NETSUITE_RECEIPT, source_ref="PO-1002",
        estimate_basis="test", amount=Decimal("18750.00"), currency="USD",
        exchange_rate=Decimal("1"), gl_account="6410", cost_center="CC-OPS",
        subsidiary_id="1",
    )
    service.promote("2026-06")
    period = rt.calendar.period_by_name("2026-06")
    posted, flags = rt.writeback.post_eligible(period, close_day=10)
    assert posted == 1 and flags == []
    je = rt.repo.je_for_line(rt.repo.lines(period="2026-06")[0].line_id)
    assert je.estimate_based
    assert "trust-ladder auto-post" in je.memo
