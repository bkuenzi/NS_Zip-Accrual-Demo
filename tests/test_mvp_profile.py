"""End-to-end close for the `mvp` profile (the SeatGeek dataset).

Runs the same 2026-06 cycle as the demo profile but against the standalone
SeatGeek dataset + config, asserting each vendor archetype lands where the
walkthrough narration promises. Guards against dataset/config drift.
"""

from decimal import Decimal

import pytest

from accrual_agent.config import Settings
from accrual_agent.engine.close_cycle import CloseCycleRunner
from accrual_agent.models import AccrualStatus, EscalationReason, ThreadStatus
from accrual_agent.runtime import Runtime
from conftest import simulated_now


@pytest.fixture
def mvp_settings(tmp_path) -> Settings:
    base = Settings(_env_file=None)
    return base.model_copy(update={
        "mode": "mock",
        "profile": "mvp",
        "outbound_mode": "dry_run",
        "db_path": str(tmp_path / "mvp.db"),
        "output_dir": str(tmp_path / "output"),
        "artifacts_dir": str(tmp_path / "artifacts"),
        "team_lead_email": "lead@seatgeek.example",
        "escalation_channels": "email",
    })


def run_day(settings, day: int):
    rt = Runtime(settings, now_provider=lambda: simulated_now(day, settings))
    return rt, CloseCycleRunner(rt).run(close_day=day)


def by_vendor(rt, period="2026-06"):
    return {ln.vendor_id: ln for ln in rt.repo.lines(period=period)}


def test_mvp_profile_uses_seatgeek_data(mvp_settings):
    rt = Runtime(mvp_settings)
    assert rt.settings.effective_company_name == "SeatGeek, Inc."
    assert rt.gl_store.accrued_liability_account == "21000"
    vendor_names = {v.name for v in rt.adapters.netsuite.get_vendors()}
    assert "Amazon Web Services, Inc." in vendor_names
    assert "Snowflake Inc." in vendor_names


def test_mvp_full_close_cycle(mvp_settings):
    # ── Day 1: identification + provisional API accruals + initial requests ──
    rt, result = run_day(mvp_settings, 1)
    assert result.ok
    lines = by_vendor(rt)
    assert len(lines) == 13

    assert lines["V-AWS"].amount == Decimal("487500.00")         # receipts-not-billed
    assert lines["V-TTD"].amount == Decimal("420000.00")         # Zip committed spend
    assert lines["V-STORMFACT"].currency == "GBP"               # GBP subsidiary
    assert lines["V-STORMFACT"].base_amount == Decimal("91584.00")   # 72000 * 1.272
    assert lines["V-APEXSTAFF"].gl_account is None              # unmapped vendor
    assert lines["V-RXR"].thread_status == ThreadStatus.BLOCKED_NO_CONTACT

    # ad-platform actuals net of billed, auto-confirmed
    assert lines["V-GOOGLE"].status == AccrualStatus.AUTO_CONFIRMED
    assert lines["V-GOOGLE"].comm_suppressed
    assert lines["V-META"].status == AccrualStatus.AUTO_CONFIRMED

    # ── Day 3: AWS + Trade Desk confirm by email ──
    rt, _ = run_day(mvp_settings, 3)
    lines = by_vendor(rt)
    assert lines["V-AWS"].status == AccrualStatus.POSTED
    assert lines["V-TTD"].status == AccrualStatus.POSTED

    # ── Day 5: Stormfactory routes through the LLM fallback (EU number format) ──
    rt, _ = run_day(mvp_settings, 5)
    lines = by_vendor(rt)
    assert lines["V-STORMFACT"].confirmed_amount == Decimal("72000.00")
    assert lines["V-STORMFACT"].status == AccrualStatus.POSTED
    assert lines["V-IHEART"].status == AccrualStatus.POSTED     # prorated service PO
    assert lines["V-META"].status == AccrualStatus.POSTED       # settled ad figure

    # ── Day 7: Snowflake true-up breaches the 5% gate -> held ──
    rt, _ = run_day(mvp_settings, 7)
    lines = by_vendor(rt)
    assert lines["V-SNOWFLAKE"].status == AccrualStatus.HELD_FOR_REVIEW
    assert lines["V-SNOWFLAKE"].confirmed_amount == Decimal("212400.00")
    assert lines["V-AWS"].status == AccrualStatus.CLEARED       # July invoice arrived

    # ── Day 10 (first pass): Stripe exhausts the ladder, flagged close-risk ──
    rt, _ = run_day(mvp_settings, 10)
    lines = by_vendor(rt)
    assert lines["V-STRIPE"].status == AccrualStatus.ESTIMATED
    assert lines["V-STRIPE"].close_risk
    assert rt.repo.sent_stages(lines["V-STRIPE"].line_id) == {
        "initial", "day3", "day7", "day10"
    }

    # ── Controller approves the held Snowflake variance -> posts at vendor figure ──
    rt = Runtime(mvp_settings, now_provider=lambda: simulated_now(10, mvp_settings))
    snow = by_vendor(rt)["V-SNOWFLAKE"]
    snow = rt.register.transition(
        snow, AccrualStatus.CONFIRMED, actor="controller@seatgeek.example",
        source="review", hold_reason=None,
    )
    je_id = rt.writeback.post_single(snow, rt.calendar.period_by_name("2026-06"))
    assert je_id
    assert rt.repo.je_for_line(snow.line_id).amount == Decimal("212400.00")

    # ── Final cycle: Snowflake's invoice clears; escalations stand for the rest ──
    rt, _ = run_day(mvp_settings, 10)
    lines = by_vendor(rt)
    assert lines["V-SNOWFLAKE"].status == AccrualStatus.CLEARED
    assert lines["V-STRIPE"].thread_status == ThreadStatus.EXHAUSTED

    open_reasons = {(e.reason, e.line_id) for e in rt.repo.open_escalations()}
    stripe = lines["V-STRIPE"].line_id
    apex = lines["V-APEXSTAFF"].line_id
    rxr = lines["V-RXR"].line_id
    assert (EscalationReason.VENDOR_NON_RESPONSIVE, stripe) in open_reasons
    assert (EscalationReason.UNMAPPED_VENDOR, apex) in open_reasons
    assert (EscalationReason.MISSING_CONTACT, rxr) in open_reasons

    # Estimates never posted: Stripe/Apex/RXR have no JEs
    for line_id in (stripe, apex, rxr):
        assert rt.repo.je_for_line(line_id) is None
