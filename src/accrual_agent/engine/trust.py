"""Trust ladder: measured accuracy earns per-vendor estimate auto-posting.

A vendor's streak counts consecutive recent periods in which every cleared
accrual landed within ACCRUAL_TRUST_TOLERANCE_PCT of the actual invoice
(recorded at reconcile time as ``cleared_invoice_amount``). A streak of
ACCRUAL_TRUST_STREAK_PERIODS makes the vendor eligible: on the final close
day its still-unconfirmed estimates auto-post instead of queueing for
approval. Any miss resets the streak; a controller can revoke a vendor via
``trust_ladder.revoked`` in gl_mappings.yaml. The JE memo labels these posts
ESTIMATE-BASED (trust-ladder auto-post) so nothing hides in the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..config import GLMappingStore, Settings
from ..logging_setup import get_logger
from ..models import AccrualLine, AccrualStatus
from ..register.service import RegisterService
from .confirmation import variance_pct

log = get_logger(__name__)


@dataclass
class PeriodAccuracy:
    period: str
    max_variance_pct: Decimal
    accurate: bool


@dataclass
class VendorStreak:
    vendor_id: str
    vendor_name: str
    streak: int
    required: int
    revoked: bool
    periods: list[PeriodAccuracy] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return self.streak >= self.required and not self.revoked


class TrustLadderService:
    def __init__(
        self, settings: Settings, register: RegisterService, gl_store: GLMappingStore
    ) -> None:
        self.settings = settings
        self.register = register
        self.gl_store = gl_store

    def streaks(self) -> list[VendorStreak]:
        """Per-vendor accuracy history from cleared lines, newest period last."""
        cleared = [
            ln for ln in self.register.repo.lines(statuses=[AccrualStatus.CLEARED])
            if ln.cleared_invoice_amount is not None and ln.vendor_id != "SUNDRY"
        ]
        by_vendor: dict[str, list[AccrualLine]] = {}
        for ln in cleared:
            by_vendor.setdefault(ln.vendor_id, []).append(ln)

        tolerance = self.settings.trust_tolerance_pct
        results: list[VendorStreak] = []
        for vendor_id, lines in sorted(by_vendor.items()):
            by_period: dict[str, list[AccrualLine]] = {}
            for ln in lines:
                by_period.setdefault(ln.period, []).append(ln)
            history = [
                PeriodAccuracy(
                    period=period,
                    max_variance_pct=(
                        max_var := max(
                            variance_pct(ln.postable_amount, ln.cleared_invoice_amount)
                            for ln in period_lines
                        )
                    ),
                    accurate=max_var <= tolerance,
                )
                for period, period_lines in sorted(by_period.items())
            ]
            streak = 0
            for acc in reversed(history):     # consecutive from the most recent
                if not acc.accurate:
                    break
                streak += 1
            results.append(VendorStreak(
                vendor_id=vendor_id,
                vendor_name=lines[0].vendor_name,
                streak=streak,
                required=self.settings.trust_streak_periods,
                revoked=vendor_id in self.gl_store.trust_revoked,
                periods=history,
            ))
        return results

    def eligible_vendor_ids(self) -> set[str]:
        return {s.vendor_id for s in self.streaks() if s.eligible}

    def promote(self, period_name: str) -> int:
        """Final close day: auto-confirm unconfirmed estimates of eligible
        vendors so the posting stage books them as trust-ladder JEs."""
        eligible = self.eligible_vendor_ids()
        if not eligible:
            return 0
        promoted = 0
        for line in self.register.unconfirmed_lines(period_name):
            if line.vendor_id not in eligible:
                continue
            self.register.transition(
                line, AccrualStatus.CONFIRMED, source="trust_ladder",
                confirmed_source="trust_ladder",
                notes=(
                    f"auto-posted via trust ladder: {line.vendor_id} estimates "
                    f"within ±{self.settings.trust_tolerance_pct}% of invoices for "
                    f"{self.settings.trust_streak_periods}+ consecutive periods"
                ),
            )
            promoted += 1
            log.info("trust_ladder.promoted", line_id=line.line_id, vendor=line.vendor_id)
        return promoted
