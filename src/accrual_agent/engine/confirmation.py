"""Applying vendor confirmations (from replies or arriving invoices).

Policy: a confirmed amount within the applicable variance threshold of the
estimate is accepted and the line confirms at the vendor's number; beyond the
threshold the line is held for human review with both amounts shown. The
threshold resolves per-vendor, then per-GL, then the global default.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import GLMappingStore, Settings
from ..logging_setup import get_logger
from ..models import (
    AccrualLine,
    AccrualStatus,
    EscalationReason,
    ParsedVendorReply,
    ThreadStatus,
)
from ..register.service import RegisterService

log = get_logger(__name__)

MIN_ACTIONABLE_CONFIDENCE = 0.35


def variance_pct(estimate: Decimal, actual: Decimal) -> Decimal:
    if estimate == 0:
        return Decimal("100")
    return (abs(actual - estimate) / abs(estimate) * 100).quantize(Decimal("0.01"))


class ConfirmationService:
    def __init__(
        self, settings: Settings, register: RegisterService, gl_store: GLMappingStore
    ) -> None:
        self.settings = settings
        self.register = register
        self.gl_store = gl_store

    def threshold_for(self, line: AccrualLine) -> Decimal:
        return self.gl_store.variance_threshold_for(
            line.vendor_id, line.gl_account, self.settings.variance_threshold_pct
        )

    # ── vendor replies ───────────────────────────────────────────────────

    def apply_reply(
        self, line: AccrualLine, parsed: ParsedVendorReply
    ) -> list[tuple[EscalationReason, str]]:
        """Update the register from a parsed reply; returns escalation flags."""
        flags: list[tuple[EscalationReason, str]] = []
        metadata: dict[str, object] = {"thread_status": ThreadStatus.REPLIED}
        if parsed.invoice_number:
            metadata["invoice_number"] = parsed.invoice_number
        if parsed.expected_invoice_date:
            metadata["invoice_eta"] = parsed.expected_invoice_date

        if parsed.confirmed_amount is None or parsed.confidence < MIN_ACTIONABLE_CONFIDENCE:
            self.register.update_fields(
                line.line_id, source="inbound",
                notes=f"reply received but not parseable "
                      f"(confidence {parsed.confidence}, method {parsed.method})",
                **metadata,
            )
            flags.append((
                EscalationReason.UNPARSEABLE_REPLY,
                f"{line.line_id}: vendor replied but no amount could be extracted "
                f"(confidence {parsed.confidence}); manual read required",
            ))
            return flags

        if line.status not in (AccrualStatus.ESTIMATED, AccrualStatus.CONFIRMED):
            # Late reply on an already-processed line: record it, change nothing.
            self.register.update_fields(line.line_id, source="inbound", **metadata)
            return flags

        if parsed.currency and parsed.currency != line.currency:
            self.register.transition(
                line, AccrualStatus.HELD_FOR_REVIEW, source="inbound",
                hold_reason=(
                    f"vendor confirmed {parsed.confirmed_amount} {parsed.currency} "
                    f"but the accrual is denominated in {line.currency}"
                ),
                **metadata,
            )
            flags.append((
                EscalationReason.VARIANCE_BREACH,
                f"{line.line_id}: currency mismatch in vendor confirmation",
            ))
            return flags

        threshold = self.threshold_for(line)
        variance = variance_pct(line.amount, parsed.confirmed_amount)
        if variance <= threshold:
            if line.status == AccrualStatus.ESTIMATED:
                self.register.transition(
                    line, AccrualStatus.CONFIRMED, source="inbound",
                    confirmed_amount=parsed.confirmed_amount,
                    confirmed_source="vendor_reply",
                    **metadata,
                )
            else:
                self.register.update_fields(
                    line.line_id, source="inbound",
                    confirmed_amount=parsed.confirmed_amount, **metadata,
                )
            log.info(
                "confirmation.accepted", line_id=line.line_id,
                estimate=str(line.amount), confirmed=str(parsed.confirmed_amount),
                variance_pct=str(variance), method=parsed.method,
            )
        else:
            self.register.transition(
                line, AccrualStatus.HELD_FOR_REVIEW, source="inbound",
                confirmed_amount=parsed.confirmed_amount,
                confirmed_source="vendor_reply",
                hold_reason=(
                    f"vendor confirmed {parsed.confirmed_amount:,.2f} {line.currency} "
                    f"vs estimate {line.amount:,.2f} — variance {variance}% exceeds "
                    f"±{threshold}% threshold"
                ),
                **metadata,
            )
            flags.append((
                EscalationReason.VARIANCE_BREACH,
                f"{line.line_id}: vendor amount {parsed.confirmed_amount:,.2f} vs "
                f"estimate {line.amount:,.2f} ({variance}% > ±{threshold}%)",
            ))
        return flags

    # ── invoice arrivals during close ────────────────────────────────────

    def apply_invoice(
        self, line: AccrualLine, invoice_number: str, amount: Decimal
    ) -> list[tuple[EscalationReason, str]]:
        """An AP bill matching an unposted accrual is the strongest
        confirmation source — same threshold gate as replies."""
        flags: list[tuple[EscalationReason, str]] = []
        threshold = self.threshold_for(line)
        variance = variance_pct(line.amount, amount)
        if variance <= threshold:
            if line.status == AccrualStatus.ESTIMATED:
                self.register.transition(
                    line, AccrualStatus.CONFIRMED, source="reconcile",
                    confirmed_amount=amount, confirmed_source="netsuite_bill",
                    invoice_number=invoice_number,
                )
            else:
                self.register.update_fields(
                    line.line_id, source="reconcile",
                    confirmed_amount=amount, invoice_number=invoice_number,
                )
        else:
            self.register.transition(
                line, AccrualStatus.HELD_FOR_REVIEW, source="reconcile",
                confirmed_amount=amount, confirmed_source="netsuite_bill",
                invoice_number=invoice_number,
                hold_reason=(
                    f"invoice {invoice_number} for {amount:,.2f} {line.currency} vs "
                    f"accrual {line.amount:,.2f} — variance {variance}% exceeds "
                    f"±{threshold}%"
                ),
            )
            flags.append((
                EscalationReason.VARIANCE_BREACH,
                f"{line.line_id}: invoice {invoice_number} {amount:,.2f} vs accrual "
                f"{line.amount:,.2f} ({variance}% > ±{threshold}%)",
            ))
        return flags
