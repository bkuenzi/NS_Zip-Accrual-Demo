"""Checkpoint exception report: concise, needs-attention-first.

Written to disk every run; on checkpoint days (Day 5 / Day 10 / final) it is
also pushed through the configured escalation channels with the dashboard
linked.
"""

from __future__ import annotations

from decimal import Decimal

from ..comms.mailer import OutboundEmail
from ..fiscal import Period
from ..logging_setup import get_logger
from ..models import ESCALATION_LABELS, AccrualStatus, RunResult, ThreadStatus
from ..runtime import Runtime

log = get_logger(__name__)

ATTENTION_ORDER = [
    AccrualStatus.HELD_FOR_REVIEW,
    AccrualStatus.ESTIMATED,
]


def build_report(
    rt: Runtime, period: Period, close_day: int, result: RunResult
) -> tuple[str, int]:
    """Returns (report_text, needs_attention_count)."""
    lines = rt.repo.lines(period=period.name)
    escalations = rt.repo.open_escalations()
    held = [ln for ln in lines if ln.status == AccrualStatus.HELD_FOR_REVIEW]
    unconfirmed = [ln for ln in lines if ln.status == AccrualStatus.ESTIMATED]
    posted = [ln for ln in lines if ln.status in (AccrualStatus.POSTED, AccrualStatus.CLEARED)]
    confirmed = [
        ln for ln in lines
        if ln.status in (AccrualStatus.CONFIRMED, AccrualStatus.AUTO_CONFIRMED)
    ]
    needs_attention = len(held) + len(unconfirmed) + len(result.errors)

    base_total = sum((ln.base_amount for ln in lines), Decimal("0"))
    posted_total = sum((ln.base_amount for ln in posted), Decimal("0"))

    out: list[str] = []
    out.append(
        f"{period.name} CLOSE — DAY {close_day} of {rt.calendar.final_close_day} — "
        f"ACCRUAL EXCEPTION REPORT"
    )
    out.append("=" * 72)
    out.append(
        f"Register: {len(lines)} lines, {base_total:,.2f} {rt.settings.base_currency} "
        f"total | posted {posted_total:,.2f} | "
        f"{len(confirmed)} confirmed awaiting post | {needs_attention} need attention"
    )
    out.append("")

    if result.errors:
        out.append("RUN ERRORS — integrations that failed this cycle")
        for err in result.errors:
            out.append(f"  ! [{err.stage}] {err.error}")
        out.append("")

    if held:
        out.append("NEEDS ATTENTION — held for review (post blocked until approved)")
        for ln in held:
            out.append(
                f"  * {ln.line_id} {ln.vendor_name}: {ln.hold_reason or 'held'}"
            )
        out.append("")

    if unconfirmed:
        out.append("NEEDS ATTENTION — still unconfirmed")
        for ln in unconfirmed:
            status_bits = []
            if ln.thread_status == ThreadStatus.BLOCKED_NO_CONTACT:
                status_bits.append("send BLOCKED: no verified contact")
            elif ln.thread_status == ThreadStatus.EXHAUSTED:
                status_bits.append("vendor non-responsive after final reminder")
            else:
                sent = sorted(rt.repo.sent_stages(ln.line_id))
                status_bits.append(f"outreach: {', '.join(sent) if sent else 'pending'}")
            if ln.gl_account is None:
                status_bits.append("NO GL MAPPING")
            if ln.close_risk:
                status_bits.append("CLOSE RISK")
            out.append(
                f"  * {ln.line_id} {ln.vendor_name} "
                f"{ln.amount:,.2f} {ln.currency} — {'; '.join(status_bits)}"
            )
        out.append("")

    if escalations:
        out.append("OPEN ESCALATIONS")
        for esc in escalations:
            out.append(
                f"  * [{esc.severity.upper()}] {ESCALATION_LABELS[esc.reason]}"
                f"{f' — {esc.line_id}' if esc.line_id else ''} "
                f"(raised x{esc.raise_count})"
            )
        out.append("")

    out.append("POSTED / CLEARED")
    if posted:
        for ln in posted:
            je = rt.repo.je_for_line(ln.line_id)
            cleared = f", cleared vs {ln.invoice_number}" if (
                ln.status == AccrualStatus.CLEARED
            ) else ""
            out.append(
                f"  - {ln.line_id} {ln.vendor_name} {ln.postable_amount:,.2f} "
                f"{ln.currency} (JE {je.netsuite_id if je else '—'}{cleared})"
            )
    else:
        out.append("  (none yet)")
    out.append("")
    out.append(
        f"Cycle stats: +{result.lines_created} new lines, {result.emails_sent} emails, "
        f"{result.replies_processed} replies, {result.jes_posted} JEs, "
        f"{result.lines_cleared} cleared, {result.escalations_raised} escalations"
    )
    return "\n".join(out), needs_attention


def distribute_checkpoint(
    rt: Runtime,
    period: Period,
    close_day: int,
    report_text: str,
    needs_attention: int,
    dashboard_path: str,
) -> None:
    subject, body = rt.templates.render_email(
        "checkpoint_report.j2",
        period=period.name,
        close_day=close_day,
        final_close_day=rt.calendar.final_close_day,
        needs_attention_count=needs_attention,
        report_body=report_text,
        dashboard_path=dashboard_path,
    )
    channels = rt.settings.escalation_channel_list
    if "email" in channels and rt.settings.team_lead_email:
        rt.mailer.send(OutboundEmail(
            to=rt.settings.team_lead_email, subject=subject, body=body
        ))
    if "slack" in channels:
        rt.escalation._post_slack(f"*{subject}*\n```{report_text[:2800]}```")
    log.info("checkpoint.distributed", close_day=close_day, channels=channels)
