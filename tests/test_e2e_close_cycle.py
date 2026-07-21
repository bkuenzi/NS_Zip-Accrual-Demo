"""End-to-end mock close: the full 2026-06 cycle across close days.

Walks the same script as `accrual-agent demo` and asserts the terminal state
of every seeded scenario path.
"""

from decimal import Decimal
from pathlib import Path

from accrual_agent.engine.close_cycle import CloseCycleRunner
from accrual_agent.models import AccrualStatus, EscalationReason, ThreadStatus
from accrual_agent.runtime import Runtime
from conftest import simulated_now


def run_day(settings, day: int):
    rt = Runtime(settings, now_provider=lambda: simulated_now(day, settings))
    return rt, CloseCycleRunner(rt).run(close_day=day)


def by_vendor(rt, period="2026-06"):
    return {ln.vendor_id: ln for ln in rt.repo.lines(period=period)}


def test_full_close_cycle(settings):
    # ── Day 1: identification + provisional API accruals + initial requests ──
    rt, result = run_day(settings, 1)
    assert result.ok
    lines = by_vendor(rt)
    assert len(lines) == 11

    assert lines["V-ACME"].amount == Decimal("28500.00")        # receipts-not-billed
    assert lines["V-ETA"].amount == Decimal("19565.22")         # prorated service PO
    assert lines["V-GAMMA"].amount == Decimal("15000.00")       # Zip committed spend
    assert lines["SUNDRY"].amount == Decimal("1020.00")         # aggregated sub-floor gaps
    assert lines["SUNDRY"].comm_suppressed
    assert lines["V-GOOGLE"].confirmed_amount == Decimal("47910.22")  # provisional − billed
    assert lines["V-GOOGLE"].status == AccrualStatus.AUTO_CONFIRMED
    assert lines["V-GOOGLE"].provisional and lines["V-GOOGLE"].comm_suppressed
    assert lines["V-META"].status == AccrualStatus.AUTO_CONFIRMED
    assert "V-IOTA" not in lines                                # below materiality floor

    assert lines["V-ZETA"].currency == "EUR"
    assert lines["V-ZETA"].exchange_rate == Decimal("1.09")
    assert lines["V-ZETA"].base_amount == Decimal("13080.00")

    assert lines["V-EPSILON"].thread_status == ThreadStatus.BLOCKED_NO_CONTACT
    assert lines["V-DELTA"].gl_account is None                  # unmapped vendor
    assert result.emails_sent == 6                              # 8 unconfirmed − 2 blocked
    assert result.jes_posted == 0                               # nothing confirmed yet

    # ── Day 3: Acme confirms and posts; ads still provisional ──
    rt, result = run_day(settings, 3)
    lines = by_vendor(rt)
    assert lines["V-ACME"].status == AccrualStatus.POSTED
    assert lines["V-ACME"].invoice_number == "INV-8801"
    assert lines["V-GOOGLE"].provisional                        # settle window open
    assert result.jes_posted == 1

    # ── Day 5: LLM-parsed Zeta + Eta confirm; settled ad figures post ──
    rt, result = run_day(settings, 5)
    lines = by_vendor(rt)
    assert lines["V-ZETA"].status == AccrualStatus.POSTED       # via LLM fallback
    assert lines["V-ZETA"].confirmed_amount == Decimal("12000.00")
    assert lines["V-ETA"].status == AccrualStatus.POSTED
    assert not lines["V-GOOGLE"].provisional
    assert lines["V-GOOGLE"].confirmed_amount == Decimal("48270.45")  # restated final
    assert lines["V-GOOGLE"].status == AccrualStatus.POSTED
    assert lines["V-META"].status == AccrualStatus.POSTED
    assert result.jes_posted == 4

    # ── Day 7: disputes hold; Acme's arrived invoice clears it ──
    rt, result = run_day(settings, 7)
    lines = by_vendor(rt)
    assert lines["V-GAMMA"].status == AccrualStatus.HELD_FOR_REVIEW
    assert lines["V-GAMMA"].confirmed_amount == Decimal("17800.00")
    assert lines["V-THETA"].status == AccrualStatus.HELD_FOR_REVIEW
    assert lines["V-ACME"].status == AccrualStatus.CLEARED
    assert result.jes_posted == 0                               # holds never post

    # ── Day 10 (final): Beta exhausted, close risks flagged ──
    rt, result = run_day(settings, 10)
    lines = by_vendor(rt)
    assert lines["V-BETA"].status == AccrualStatus.ESTIMATED
    assert lines["V-BETA"].close_risk
    assert rt.repo.sent_stages(lines["V-BETA"].line_id) == {
        "initial", "day3", "day7", "day10"
    }
    assert lines["V-ZETA"].status == AccrualStatus.CLEARED      # RE-2211 arrived

    # ── Human approvals: held lines post immediately ──
    rt = Runtime(settings, now_provider=lambda: simulated_now(10, settings))
    for vendor in ("V-GAMMA", "V-THETA"):
        line = by_vendor(rt)[vendor]
        line = rt.register.transition(
            line, AccrualStatus.CONFIRMED, actor="controller@yourco.example",
            source="review", hold_reason=None,
        )
        je_id = rt.writeback.post_single(line, rt.calendar.period_by_name("2026-06"))
        assert je_id
    lines = by_vendor(rt)
    assert lines["V-GAMMA"].status == AccrualStatus.POSTED
    assert rt.repo.je_for_line(lines["V-GAMMA"].line_id).amount == Decimal("17800.00")

    # ── Final cycle: Theta's invoice clears; escalations stand for the rest ──
    rt, result = run_day(settings, 10)
    lines = by_vendor(rt)
    assert lines["V-THETA"].status == AccrualStatus.CLEARED
    assert lines["V-BETA"].thread_status == ThreadStatus.EXHAUSTED

    open_reasons = {(e.reason, e.line_id) for e in rt.repo.open_escalations()}
    beta, delta, epsilon = (
        lines["V-BETA"].line_id, lines["V-DELTA"].line_id, lines["V-EPSILON"].line_id
    )
    assert (EscalationReason.VENDOR_NON_RESPONSIVE, beta) in open_reasons
    assert (EscalationReason.UNMAPPED_VENDOR, delta) in open_reasons
    assert (EscalationReason.MISSING_CONTACT, epsilon) in open_reasons
    assert (EscalationReason.CLOSE_RISK, beta) in open_reasons

    # Exactly the 7 expected JEs, all with unique NetSuite ids & external ids
    jes = rt.repo.journal_entries()
    assert len(jes) == 7
    assert len({j.external_id for j in jes}) == 7
    assert len({j.netsuite_id for j in jes}) == 7

    # Estimates never posted: Beta/Delta/Epsilon/sundry have no JEs
    for line_id in (beta, delta, epsilon, lines["SUNDRY"].line_id):
        assert rt.repo.je_for_line(line_id) is None

    # Reports + dashboard produced
    assert Path(result.dashboard_path).exists()
    assert Path(result.checkpoint_report_path).exists()
    report = Path(result.checkpoint_report_path).read_text()
    assert "Beta Logistics" in report and "NEEDS ATTENTION" in report


def test_rerun_is_idempotent(settings):
    """Running the same close day twice changes nothing material."""
    run_day(settings, 1)
    rt, _ = run_day(settings, 3)
    jes_before = len(rt.repo.journal_entries())
    emails_before = sum(
        len(rt.repo.comms_for_line(ln.line_id)) for ln in rt.repo.lines()
    )
    rt, result = run_day(settings, 3)
    assert result.jes_posted == 0
    assert len(rt.repo.journal_entries()) == jes_before
    emails_after = sum(
        len(rt.repo.comms_for_line(ln.line_id)) for ln in rt.repo.lines()
    )
    assert emails_after == emails_before


def test_api_failure_isolates_and_escalates(settings):
    """A dead ad platform never stalls the rest of the close."""
    rt = Runtime(settings, now_provider=lambda: simulated_now(1, settings))
    for platform in rt.adapters.ad_platforms:
        platform.fail_next_pull = True
    result = CloseCycleRunner(rt).run(close_day=1)
    lines = by_vendor(rt)
    assert len(lines) == 9                       # NetSuite/Zip paths unaffected
    assert "V-GOOGLE" not in lines
    open_reasons = {e.reason for e in rt.repo.open_escalations()}
    assert EscalationReason.API_FAILURE in open_reasons
    assert result.emails_sent > 0                # comms still ran
