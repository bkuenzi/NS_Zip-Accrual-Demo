"""Escalations to the accounting team lead.

Dedupe policy: one escalation per (line, reason). A new issue dispatches
immediately through the configured channels (email / Slack / both); an
unresolved issue re-raises with elevated urgency once it has been stale for
the configured interval (default 2 business days) or at a checkpoint day.
Resolved conditions auto-close their escalations.
"""

from __future__ import annotations

import datetime as dt
import re

import httpx

from ..comms.mailer import OutboundEmail
from ..comms.templates import TemplateEngine
from ..config import Settings
from ..fiscal import FiscalCalendar
from ..logging_setup import get_logger
from ..models import (
    ESCALATION_LABELS,
    AccrualLine,
    AccrualStatus,
    Escalation,
    EscalationReason,
    ThreadStatus,
)
from ..register.repository import Repository
from ..register.service import RegisterService

log = get_logger(__name__)

LINE_ID_RE = re.compile(r"\b(ACR-\d{4}-\d{2}-\d{4})\b")

SEVERITY: dict[EscalationReason, str] = {
    EscalationReason.VENDOR_NON_RESPONSIVE: "high",
    EscalationReason.API_FAILURE: "high",
    EscalationReason.API_ANOMALY: "high",
    EscalationReason.VARIANCE_BREACH: "medium",
    EscalationReason.UNMAPPED_VENDOR: "medium",
    EscalationReason.MISSING_CONTACT: "medium",
    EscalationReason.UNRESOLVED_SUBSIDIARY: "medium",
    EscalationReason.STALE_ACCRUAL: "high",
    EscalationReason.UNPARSEABLE_REPLY: "low",
    EscalationReason.AMBIGUOUS_INVOICE_MATCH: "medium",
    EscalationReason.CLOSE_RISK: "high",
}

SUGGESTED_ACTIONS: dict[EscalationReason, str] = {
    EscalationReason.VENDOR_NON_RESPONSIVE:
        "Contact the vendor through your account manager; approve the estimate "
        "via `accrual-agent review approve` if the amount must book at close.",
    EscalationReason.API_FAILURE:
        "Check credentials/status for the platform; re-run the cycle once restored.",
    EscalationReason.API_ANOMALY:
        "Inspect the platform data and the account mapping in gl_mappings.yaml.",
    EscalationReason.VARIANCE_BREACH:
        "Compare both amounts in `accrual-agent review list`, then approve or reject.",
    EscalationReason.UNMAPPED_VENDOR:
        "Add the vendor's GL account and cost center to config/gl_mappings.yaml.",
    EscalationReason.MISSING_CONTACT:
        "Add a verified contact: `accrual-agent contacts add <vendor> <email> --name ...`.",
    EscalationReason.UNRESOLVED_SUBSIDIARY:
        "Map the Zip business unit or ad account to a subsidiary in gl_mappings.yaml.",
    EscalationReason.STALE_ACCRUAL:
        "Chase the invoice with the vendor or reverse the accrual manually.",
    EscalationReason.UNPARSEABLE_REPLY:
        "Read the vendor's reply in the comm log and enter the confirmation manually.",
    EscalationReason.AMBIGUOUS_INVOICE_MATCH:
        "Pick the correct bill and clear the line manually.",
    EscalationReason.CLOSE_RISK:
        "Decide before close: approve the estimate to book it, or reject the line.",
}


class EscalationService:
    def __init__(
        self,
        settings: Settings,
        repo: Repository,
        register: RegisterService,
        mailer,
        templates: TemplateEngine,
        calendar: FiscalCalendar,
    ) -> None:
        self.settings = settings
        self.repo = repo
        self.register = register
        self.mailer = mailer
        self.templates = templates
        self.calendar = calendar

    def process(
        self,
        flags: list[tuple[EscalationReason, str]],
        close_day: int,
        now: dt.datetime,
    ) -> int:
        """Raise/dedupe/re-raise from this run's flags plus register state.

        Returns the number of escalations dispatched to humans this run.
        """
        flags = list(flags) + self._state_flags(close_day)
        seen_reasons = {reason for reason, _ in flags}
        self._auto_resolve(seen_reasons)

        is_checkpoint = close_day in self.settings.checkpoint_days or (
            close_day >= self.calendar.final_close_day
        )
        dispatched = 0
        for reason, detail in flags:
            line_id = m.group(1) if (m := LINE_ID_RE.search(detail)) else None
            existing = self.repo.get_escalation(line_id, reason)
            if existing is None or existing.resolved_at is not None:
                esc = self.repo.upsert_escalation(Escalation(
                    line_id=line_id,
                    reason=reason,
                    severity=SEVERITY[reason],
                    detail=detail,
                    suggested_action=SUGGESTED_ACTIONS[reason],
                    channels=self.settings.escalation_channel_list,
                ))
                self._dispatch(esc, close_day, reraised=False)
                dispatched += 1
                continue

            stale_after = self.calendar.add_business_days(
                existing.last_raised_at.date(),
                self.settings.escalation_reraise_business_days,
            )
            if now.date() >= stale_after or is_checkpoint:
                self.repo.touch_escalation(line_id, reason)
                bumped = existing.model_copy(update={
                    "severity": "high", "detail": detail,
                })
                self._dispatch(bumped, close_day, reraised=True)
                dispatched += 1
        return dispatched

    # ── register-state derived flags ─────────────────────────────────────

    def _state_flags(self, close_day: int) -> list[tuple[EscalationReason, str]]:
        flags: list[tuple[EscalationReason, str]] = []
        for line in self.repo.lines():
            if line.status == AccrualStatus.ESTIMATED:
                if line.thread_status == ThreadStatus.EXHAUSTED:
                    flags.append((
                        EscalationReason.VENDOR_NON_RESPONSIVE,
                        f"{line.line_id}: {line.vendor_name} silent after the full "
                        f"reminder ladder ({line.amount:,.2f} {line.currency} at stake)",
                    ))
                if line.thread_status == ThreadStatus.BLOCKED_NO_CONTACT:
                    flags.append((
                        EscalationReason.MISSING_CONTACT,
                        f"{line.line_id}: no verified contact for {line.vendor_name}; "
                        "outbound request is blocked",
                    ))
                if line.gl_account is None:
                    flags.append((
                        EscalationReason.UNMAPPED_VENDOR,
                        f"{line.line_id}: {line.vendor_name} has no GL mapping",
                    ))
                if line.subsidiary_id is None:
                    flags.append((
                        EscalationReason.UNRESOLVED_SUBSIDIARY,
                        f"{line.line_id}: {line.vendor_name} subsidiary unresolved",
                    ))
                if line.close_risk and close_day >= self.calendar.final_close_day:
                    flags.append((
                        EscalationReason.CLOSE_RISK,
                        f"{line.line_id}: {line.vendor_name} "
                        f"({line.amount:,.2f} {line.currency}) still unconfirmed on "
                        "the final close day",
                    ))
            elif line.status == AccrualStatus.HELD_FOR_REVIEW:
                flags.append((
                    EscalationReason.VARIANCE_BREACH,
                    f"{line.line_id}: {line.hold_reason or 'held for review'}",
                ))
        return flags

    def _auto_resolve(self, seen_reasons: set[EscalationReason]) -> None:
        for esc in self.repo.open_escalations():
            if esc.line_id is None:
                # Run-level API issues: resolved once a run stops flagging them.
                if esc.reason not in seen_reasons:
                    self.repo.resolve_escalation(esc.line_id, esc.reason)
                continue
            line = self.repo.get_line(esc.line_id)
            if line is not None and _condition_cleared(esc.reason, line):
                self.repo.resolve_escalation(esc.line_id, esc.reason)
                log.info("escalation.resolved", line_id=esc.line_id, reason=esc.reason.value)

    # ── delivery ─────────────────────────────────────────────────────────

    def _dispatch(self, esc: Escalation, close_day: int, reraised: bool) -> None:
        line = self.repo.get_line(esc.line_id) if esc.line_id else None
        subject, body = self.templates.render_email(
            "escalation.j2",
            reraised=reraised,
            reason_label=ESCALATION_LABELS[esc.reason],
            severity=esc.severity,
            vendor_name=line.vendor_name if line else "—",
            vendor_id=line.vendor_id if line else "—",
            line_id=esc.line_id or "run-level",
            ref_token=line.ref_token if line else "",
            amount=f"{line.amount:,.2f}" if line else "—",
            currency=line.currency if line else "",
            period=line.period if line else "current",
            close_day=close_day,
            final_close_day=self.calendar.final_close_day,
            detail=esc.detail,
            suggested_action=esc.suggested_action,
        )
        channels = self.settings.escalation_channel_list
        if "email" in channels and self.settings.team_lead_email:
            self.mailer.send(OutboundEmail(
                to=self.settings.team_lead_email, subject=subject, body=body
            ))
        if "slack" in channels:
            self._post_slack(f"*{subject}*\n```{body}```")
        log.info(
            "escalation.dispatched", reason=esc.reason.value, line_id=esc.line_id,
            severity=esc.severity, reraised=reraised, channels=channels,
        )

    def _post_slack(self, text: str) -> None:
        if self.settings.mode == "mock" or not self.settings.slack_webhook_url:
            log.info("escalation.slack_skipped", mode=self.settings.mode)
            return
        try:
            httpx.post(self.settings.slack_webhook_url, json={"text": text}, timeout=10)
        except httpx.HTTPError as exc:
            log.warning("escalation.slack_failed", error=str(exc))


def _condition_cleared(reason: EscalationReason, line: AccrualLine) -> bool:
    resolved_statuses = {
        AccrualStatus.CONFIRMED, AccrualStatus.AUTO_CONFIRMED,
        AccrualStatus.POSTED, AccrualStatus.CLEARED, AccrualStatus.REJECTED,
    }
    match reason:
        case EscalationReason.MISSING_CONTACT:
            return (
                line.thread_status != ThreadStatus.BLOCKED_NO_CONTACT
                or line.status in resolved_statuses
            )
        case EscalationReason.VARIANCE_BREACH:
            return line.status != AccrualStatus.HELD_FOR_REVIEW
        case EscalationReason.UNMAPPED_VENDOR:
            return line.gl_account is not None
        case EscalationReason.UNRESOLVED_SUBSIDIARY:
            return line.subsidiary_id is not None
        case (
            EscalationReason.VENDOR_NON_RESPONSIVE
            | EscalationReason.CLOSE_RISK
            | EscalationReason.UNPARSEABLE_REPLY
        ):
            return (
                line.thread_status == ThreadStatus.REPLIED
                and line.status in resolved_statuses
            ) or line.status in resolved_statuses
        case EscalationReason.STALE_ACCRUAL | EscalationReason.AMBIGUOUS_INVOICE_MATCH:
            return line.status == AccrualStatus.CLEARED
        case _:
            return False
