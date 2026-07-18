"""Register management: line creation, legal status transitions, review queue."""

from __future__ import annotations

import getpass
from decimal import Decimal

from ..logging_setup import get_logger
from ..models import AccrualLine, AccrualStatus, SourceType, ThreadStatus
from .repository import Repository

log = get_logger(__name__)

SYSTEM_ACTOR = "accrual-agent"

# The only lifecycle moves the register will accept. Anything else is a bug or
# an attempt to bypass a gate (e.g. posting an estimate), and raises.
LEGAL_TRANSITIONS: dict[AccrualStatus, set[AccrualStatus]] = {
    AccrualStatus.ESTIMATED: {
        AccrualStatus.CONFIRMED,
        AccrualStatus.AUTO_CONFIRMED,
        AccrualStatus.HELD_FOR_REVIEW,
        AccrualStatus.REJECTED,
    },
    AccrualStatus.AUTO_CONFIRMED: {
        AccrualStatus.AUTO_CONFIRMED,      # provisional re-pull adjusts amount
        AccrualStatus.HELD_FOR_REVIEW,
        AccrualStatus.POSTED,
        AccrualStatus.REJECTED,
    },
    AccrualStatus.CONFIRMED: {
        AccrualStatus.HELD_FOR_REVIEW,
        AccrualStatus.POSTED,
        AccrualStatus.REJECTED,
    },
    AccrualStatus.HELD_FOR_REVIEW: {
        AccrualStatus.CONFIRMED,           # human approval
        AccrualStatus.REJECTED,
    },
    AccrualStatus.POSTED: {
        AccrualStatus.CLEARED,
    },
    AccrualStatus.CLEARED: set(),
    AccrualStatus.REJECTED: set(),
}


class IllegalTransitionError(RuntimeError):
    pass


def current_actor(override: str | None = None) -> str:
    if override:
        return override
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


class RegisterService:
    def __init__(self, repo: Repository):
        self.repo = repo

    # ── creation / upsert ────────────────────────────────────────────────

    def upsert_line(
        self,
        *,
        vendor_id: str,
        vendor_name: str,
        period: str,
        source_type: SourceType,
        source_ref: str,
        estimate_basis: str,
        amount: Decimal,
        currency: str,
        exchange_rate: Decimal,
        gl_account: str | None,
        cost_center: str | None,
        subsidiary_id: str | None,
        provisional: bool = False,
        comm_suppressed: bool = False,
        actor: str = SYSTEM_ACTOR,
        source: str = "identification",
    ) -> tuple[AccrualLine, bool]:
        """Create the line if new; refresh the estimate if it already exists.

        Returns (line, created). Estimate refreshes only apply while the line
        is still pre-posting (estimated / auto_confirmed provisional) — a
        confirmed or posted line's amount is never silently rewritten.
        """
        key = self.repo.natural_key(source_type, source_ref, period)
        existing = self.repo.get_line_by_natural_key(key)
        base_amount = (amount * exchange_rate).quantize(Decimal("0.01"))

        if existing is None:
            line_id = self.repo.next_line_id(period)
            line = AccrualLine(
                line_id=line_id,
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                period=period,
                source_type=source_type,
                source_ref=source_ref,
                estimate_basis=estimate_basis,
                amount=amount,
                currency=currency,
                exchange_rate=exchange_rate,
                base_amount=base_amount,
                gl_account=gl_account,
                cost_center=cost_center,
                subsidiary_id=subsidiary_id,
                provisional=provisional,
                comm_suppressed=comm_suppressed,
                ref_token=f"[{line_id}]",
                thread_status=(
                    ThreadStatus.SUPPRESSED_API if comm_suppressed else ThreadStatus.NOT_STARTED
                ),
            )
            self.repo.insert_line(line)
            self.repo.add_audit(
                line.line_id, actor, source, "status", None, line.status.value
            )
            log.info(
                "register.line_created",
                line_id=line.line_id, vendor=vendor_id, amount=str(amount),
                currency=currency, source_type=source_type.value,
            )
            return line, True

        refreshable = existing.status == AccrualStatus.ESTIMATED or (
            existing.status == AccrualStatus.AUTO_CONFIRMED and existing.provisional
        )
        if refreshable and (
            existing.amount != amount or existing.exchange_rate != exchange_rate
        ):
            self.repo.update_line_fields(
                existing.line_id, actor, source,
                amount=amount,
                exchange_rate=exchange_rate,
                base_amount=base_amount,
                estimate_basis=estimate_basis,
            )
            refreshed = self.repo.get_line(existing.line_id)
            assert refreshed is not None
            return refreshed, False
        return existing, False

    # ── transitions ──────────────────────────────────────────────────────

    def transition(
        self,
        line: AccrualLine,
        new_status: AccrualStatus,
        *,
        actor: str = SYSTEM_ACTOR,
        source: str = "engine",
        **extra_fields: object,
    ) -> AccrualLine:
        allowed = LEGAL_TRANSITIONS.get(line.status, set())
        if new_status not in allowed:
            raise IllegalTransitionError(
                f"{line.line_id}: illegal transition {line.status.value} -> "
                f"{new_status.value} (allowed: {sorted(s.value for s in allowed)})"
            )
        self.repo.update_line_fields(
            line.line_id, actor, source, status=new_status, **extra_fields
        )
        updated = self.repo.get_line(line.line_id)
        assert updated is not None
        log.info(
            "register.transition",
            line_id=line.line_id, old=line.status.value, new=new_status.value,
            actor=actor, source=source,
        )
        return updated

    def update_fields(
        self, line_id: str, *, actor: str = SYSTEM_ACTOR, source: str = "engine",
        **fields: object,
    ) -> AccrualLine:
        self.repo.update_line_fields(line_id, actor, source, **fields)
        updated = self.repo.get_line(line_id)
        assert updated is not None
        return updated

    # ── queries ──────────────────────────────────────────────────────────

    def review_queue(self, period: str | None = None) -> list[AccrualLine]:
        held = self.repo.lines(period=period, statuses=[AccrualStatus.HELD_FOR_REVIEW])
        blocked = [
            ln for ln in self.repo.lines(period=period, statuses=[AccrualStatus.ESTIMATED])
            if ln.thread_status == ThreadStatus.BLOCKED_NO_CONTACT or ln.close_risk
            or ln.gl_account is None or ln.subsidiary_id is None
        ]
        return held + blocked

    def postable_lines(self, period: str) -> list[AccrualLine]:
        return self.repo.lines(
            period=period,
            statuses=[AccrualStatus.CONFIRMED, AccrualStatus.AUTO_CONFIRMED],
        )

    def unconfirmed_lines(self, period: str) -> list[AccrualLine]:
        return self.repo.lines(period=period, statuses=[AccrualStatus.ESTIMATED])

    def posted_lines(self, period: str) -> list[AccrualLine]:
        return self.repo.lines(period=period, statuses=[AccrualStatus.POSTED])
