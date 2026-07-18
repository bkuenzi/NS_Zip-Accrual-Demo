"""NetSuite write-back: accrual JEs, reversal verification, invoice clearing.

Hard gates enforced here:
  * only confirmed / auto-confirmed lines post — an estimate can only post
    after explicit human approval (which routes through CONFIRMED first)
  * provisional API lines wait for the settle window unless it's the final
    close day
  * the JE externalId is a deterministic hash of vendor|period|source_ref, so
    a duplicate post is impossible at the NetSuite boundary even if the local
    register is lost

Reversals: each JE carries reversalDate = day 1 of the next period (NetSuite
auto-reversing entry). The reconcile pass verifies the real invoice arrived
and matches, then marks the line cleared; posted accruals unmatched beyond the
lookback window escalate as stale.
"""

from __future__ import annotations

import datetime as dt
import hashlib

from ..config import GLMappingStore, Settings
from ..fiscal import FiscalCalendar, Period
from ..integrations.base import IntegrationError
from ..integrations.factory import AdapterSet
from ..logging_setup import get_logger
from ..models import (
    AccrualLine,
    AccrualStatus,
    EscalationReason,
    JournalEntry,
    SourceType,
)
from ..register.service import RegisterService
from .confirmation import ConfirmationService, variance_pct

log = get_logger(__name__)


def je_external_id(vendor_id: str, period: str, source_ref: str) -> str:
    digest = hashlib.sha256(f"{vendor_id}|{period}|{source_ref}".encode()).hexdigest()
    return f"ACRJE-{digest[:20]}"


class WritebackService:
    def __init__(
        self,
        settings: Settings,
        adapters: AdapterSet,
        register: RegisterService,
        gl_store: GLMappingStore,
        calendar: FiscalCalendar,
        confirmation: ConfirmationService,
    ) -> None:
        self.settings = settings
        self.adapters = adapters
        self.register = register
        self.gl_store = gl_store
        self.calendar = calendar
        self.confirmation = confirmation

    # ── posting ──────────────────────────────────────────────────────────

    def post_eligible(
        self, period: Period, close_day: int
    ) -> tuple[int, list[tuple[EscalationReason, str]]]:
        posted = 0
        flags: list[tuple[EscalationReason, str]] = []
        final_day = close_day >= self.calendar.final_close_day

        for line in self.register.postable_lines(period.name):
            if line.provisional and not final_day:
                log.info("writeback.waiting_settle", line_id=line.line_id)
                continue
            blocker = self._posting_blocker(line)
            if blocker is not None:
                flags.append(blocker)
                continue
            try:
                if self._post_line(line, period):
                    posted += 1
            except IntegrationError as exc:
                flags.append((
                    EscalationReason.API_FAILURE,
                    f"{line.line_id}: JE post failed — {exc}",
                ))
        return posted, flags

    def _posting_blocker(
        self, line: AccrualLine
    ) -> tuple[EscalationReason, str] | None:
        if line.gl_account is None:
            return (
                EscalationReason.UNMAPPED_VENDOR,
                f"{line.line_id}: vendor {line.vendor_id} has no GL mapping — "
                "add it to config/gl_mappings.yaml",
            )
        if line.subsidiary_id is None:
            return (
                EscalationReason.UNRESOLVED_SUBSIDIARY,
                f"{line.line_id}: no subsidiary resolvable from the source document",
            )
        return None

    def _post_line(self, line: AccrualLine, period: Period) -> bool:
        external_id = je_external_id(line.vendor_id, line.period, line.source_ref)
        if self.register.repo.je_by_external_id(external_id) is not None:
            # Already posted in an earlier (possibly crashed) run: converge state.
            if line.status != AccrualStatus.POSTED:
                self.register.transition(line, AccrualStatus.POSTED, source="writeback")
            return False

        estimate_based = line.confirmed_source == "human_estimate_approval"
        memo = (
            f"Accrual {line.period} | {line.vendor_name} | {line.source_ref} | "
            f"{line.line_id}" + (" | ESTIMATE-BASED (approved unconfirmed)" if estimate_based else "")
        )
        je = JournalEntry(
            line_id=line.line_id,
            external_id=external_id,
            tran_date=period.end,
            reversal_date=self.calendar.next_period(period).start,
            subsidiary_id=line.subsidiary_id or "",
            debit_account=line.gl_account or "",
            credit_account=self.gl_store.accrued_liability_account,
            amount=line.postable_amount,
            currency=line.currency,
            exchange_rate=line.exchange_rate,
            memo=memo,
            estimate_based=estimate_based,
        )
        netsuite_id = self.adapters.netsuite.post_journal_entry(je)
        je.netsuite_id = netsuite_id
        je.posted_at = dt.datetime.now(dt.UTC)
        self.register.repo.add_journal_entry(je)
        self.register.transition(
            line, AccrualStatus.POSTED, source="writeback",
            notes=f"JE {netsuite_id} posted (Dr {je.debit_account} / "
                  f"Cr {je.credit_account}), auto-reverses {je.reversal_date}",
        )
        log.info(
            "writeback.posted", line_id=line.line_id, netsuite_id=netsuite_id,
            amount=str(je.amount), currency=je.currency,
            external_id=external_id, estimate_based=estimate_based,
        )
        return True

    def post_single(self, line: AccrualLine, period: Period) -> str | None:
        """Immediate post after a human approval; returns the NetSuite JE id."""
        blocker = self._posting_blocker(line)
        if blocker is not None:
            raise RuntimeError(blocker[1])
        self._post_line(line, period)
        je = self.register.repo.je_for_line(line.line_id)
        return je.netsuite_id if je else None

    # ── reversal verification / invoice clearing ─────────────────────────

    def reconcile(
        self, current_period: Period, today: dt.date
    ) -> tuple[int, list[tuple[EscalationReason, str]]]:
        """Match arriving invoices to accruals.

        * unposted lines in the current close: an arriving bill becomes the
          confirmation source (threshold-gated)
        * posted lines from the lookback window: a matching bill verifies the
          auto-reversal cycle and clears the line
        * posted lines older than the window with no match: stale escalation
        """
        cleared = 0
        flags: list[tuple[EscalationReason, str]] = []
        lookback = self.settings.reversal_lookback_periods
        scan_start = self.calendar.prior_period(current_period, back=lookback).start
        bills = self.adapters.netsuite.get_vendor_bills(scan_start, today)

        # 1. bill-based confirmation for the period being closed
        for line in self.register.unconfirmed_lines(current_period.name):
            match, ambiguity = self._match_bill(line, bills)
            if ambiguity:
                flags.append((EscalationReason.AMBIGUOUS_INVOICE_MATCH, ambiguity))
                continue
            if match is not None:
                flags.extend(
                    self.confirmation.apply_invoice(line, match.invoice_number, match.amount)
                )

        # 2. clearing for posted accruals in the lookback window
        periods = [current_period] + [
            self.calendar.prior_period(current_period, back=i)
            for i in range(1, lookback + 1)
        ]
        for period in periods:
            for line in self.register.posted_lines(period.name):
                match, ambiguity = self._match_bill(line, bills)
                if ambiguity:
                    flags.append((EscalationReason.AMBIGUOUS_INVOICE_MATCH, ambiguity))
                    continue
                if match is None:
                    continue
                self.register.transition(
                    line, AccrualStatus.CLEARED, source="reconcile",
                    invoice_number=match.invoice_number,
                    notes=(
                        f"invoice {match.invoice_number} ({match.amount:,.2f} "
                        f"{match.currency}) matched; NetSuite auto-reversal on "
                        f"{self.calendar.next_period(period).start} nets to zero"
                    ),
                )
                cleared += 1
                variance = variance_pct(line.postable_amount, match.amount)
                threshold = self.confirmation.threshold_for(line)
                if variance > threshold:
                    flags.append((
                        EscalationReason.VARIANCE_BREACH,
                        f"{line.line_id}: cleared, but invoice {match.invoice_number} "
                        f"({match.amount:,.2f}) vs posted accrual "
                        f"({line.postable_amount:,.2f}) differs {variance}% "
                        f"(> ±{threshold}%) — review the reversal net effect",
                    ))

        # 3. stale posted accruals beyond the lookback window
        stale_boundary = self.calendar.prior_period(current_period, back=lookback)
        for line in self.register.repo.lines(statuses=[AccrualStatus.POSTED]):
            if line.period < stale_boundary.name:
                flags.append((
                    EscalationReason.STALE_ACCRUAL,
                    f"{line.line_id}: posted accrual from {line.period} still has no "
                    f"matching invoice after {lookback} periods",
                ))
        return cleared, flags

    def _match_bill(self, line, bills) -> tuple[object | None, str | None]:
        """PO-backed lines match exactly on PO reference; non-PO lines match on
        vendor + period window + amount within threshold. Multiple candidates
        are never guessed between."""
        if line.source_type in (SourceType.NETSUITE_RECEIPT, SourceType.NETSUITE_PO):
            candidates = [b for b in bills if b.po_number == line.source_ref]
        else:
            threshold = self.confirmation.threshold_for(line)
            candidates = [
                b for b in bills
                if b.vendor_id == line.vendor_id and b.po_number is None
                and (b.service_period == line.period or b.service_period is None)
                and variance_pct(line.postable_amount, b.amount) <= threshold
            ]
        if not candidates:
            return None, None
        if len(candidates) > 1:
            refs = ", ".join(b.invoice_number for b in candidates)
            return None, (
                f"{line.line_id}: {len(candidates)} bills ({refs}) could match — "
                "manual selection required"
            )
        return candidates[0], None
