"""Scripted end-to-end demo of the 2026-06 close in mock mode.

Extracted from the CLI ``demo`` command so other surfaces (the web-dashboard
exporter) can replay the same walkthrough and observe register state after
each close day via the ``on_snapshot`` hook.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from decimal import Decimal

from .config import Settings
from .locking import advisory_lock
from .models import AccrualStatus, RunResult, SourceType
from .runtime import Runtime

DEMO_PERIOD_END = dt.date(2026, 6, 30)
DEMO_DAYS = (1, 3, 5, 7, 10)

NARRATION = {
    1: "Day 1 — the agent maps every uninvoiced dollar: receipts-not-billed, "
       "prorated service POs, Zip committed spend, ad-platform actuals. Six "
       "sub-floor gaps aggregate into one sundry line, and confirmation "
       "requests go out — to the vendor, or to the internal budget owner where "
       "that's the configured route.",
    3: "Day 3 — Acme confirms by email (clean heuristic parse). First reminders "
       "fire for the silent; ad spend stays provisional inside the 72h settle "
       "window.",
    5: "Day 5 checkpoint — Eta returns the structured reply block stating 32.6% "
       "delivered, which replaces straight-line proration as the estimate "
       "basis. Zeta's German-format reply routes through the LLM fallback and "
       "passes second-pass verification. Settled ad figures post.",
    7: "Day 7 — the catch: Gamma's internal engagement owner reports $17.8k "
       "burned vs the $15k estimate, and Theta's usage true-up breaches the "
       "±5% gate. The agent refuses to post either — both held with the "
       "amounts side by side.",
    10: "Day 10 (final) — Beta exhausted the reminder ladder: non-responsive "
        "escalation + close-risk flags. July invoices clear posted accruals, "
        "completing Acme's 3-period accuracy streak — it has now earned "
        "estimate auto-posting on the trust ladder.",
}

FINAL_NARRATION = (
    "Controller review — the two held variances and the sundry aggregate are "
    "approved; each click posts a correctly-formed auto-reversing JE to "
    "NetSuite. The agent did the chasing and the typing; the controller made "
    "the three calls that needed judgment."
)

# Cleared-and-invoiced accruals from prior closes (May/April) so the demo's
# trust ladder has real history to measure Acme's accuracy streak against.
TRUST_HISTORY = (
    ("2026-04", "PO-0901", Decimal("26400.00"), Decimal("26580.00")),
    ("2026-05", "PO-0952", Decimal("27900.00"), Decimal("27750.00")),
)


def seed_trust_history(rt: Runtime) -> None:
    for period, po, estimate, invoice in TRUST_HISTORY:
        line, created = rt.register.upsert_line(
            vendor_id="V-ACME", vendor_name="Acme Cloud Services",
            period=period, source_type=SourceType.NETSUITE_RECEIPT,
            source_ref=po,
            estimate_basis=f"goods receipts less bills on {po} (prior close)",
            amount=estimate, currency="USD", exchange_rate=Decimal("1"),
            gl_account="6210", cost_center="CC-ENG", subsidiary_id="1",
            actor="history-seed", source="seed",
        )
        if not created:
            continue
        line = rt.register.transition(
            line, AccrualStatus.CONFIRMED, actor="history-seed", source="seed",
            confirmed_amount=estimate, confirmed_source="vendor_reply",
        )
        line = rt.register.transition(
            line, AccrualStatus.POSTED, actor="history-seed", source="seed",
        )
        rt.register.transition(
            line, AccrualStatus.CLEARED, actor="history-seed", source="seed",
            cleared_invoice_amount=invoice,
            notes=f"prior-close accrual cleared by invoice ({invoice:,.2f} USD)",
        )

# on_snapshot(step_id, close_day, rt, result, approvals) — fired after each
# day's cycle and once more after the post-review final cycle. ``approvals``
# is the cumulative list of controller approvals ({line_id, je_id, amount,
# currency, vendor_name}); empty until the review step has run.
SnapshotHook = Callable[[str, int, Runtime, RunResult, list[dict]], None]


class _ConsoleLike(Protocol):
    def print(self, *args, **kwargs) -> None: ...
    def rule(self, *args, **kwargs) -> None: ...


def simulated_now(settings: Settings, day: int) -> dt.datetime:
    """9:00 local on the Nth business day after the demo period end."""
    from zoneinfo import ZoneInfo

    rt_probe = Runtime(settings)
    run_date = rt_probe.calendar.add_business_days(DEMO_PERIOD_END, day)
    return dt.datetime.combine(
        run_date, dt.time(9, 0), tzinfo=ZoneInfo(settings.close_timezone)
    )


def run_scripted_demo(
    settings: Settings,
    console: _ConsoleLike,
    *,
    keep: bool = False,
    on_snapshot: SnapshotHook | None = None,
    print_summary: Callable[[RunResult], None] | None = None,
) -> RunResult:
    """Run the scripted 2026-06 close walkthrough. Returns the final RunResult."""
    from .engine.close_cycle import CloseCycleRunner

    if not keep:
        for suffix in ("", "-wal", "-shm"):
            Path(str(settings.db_path) + suffix).unlink(missing_ok=True)

    def banner(text: str) -> None:
        console.rule(f"[bold cyan]{text}")

    summary = print_summary or (lambda result: None)
    approvals: list[dict] = []

    with advisory_lock(settings.db_path):
        seed_trust_history(
            Runtime(settings, now_provider=lambda: simulated_now(settings, 1))
        )
        for day in DEMO_DAYS:
            banner(f"Close day {day} — {simulated_now(settings, day).date()}")
            console.print(NARRATION[day], style="dim")
            rt = Runtime(settings, now_provider=lambda d=day: simulated_now(settings, d))
            result = CloseCycleRunner(rt).run(close_day=day)
            summary(result)
            if on_snapshot:
                on_snapshot(f"day-{day}", day, rt, result, approvals)

        banner("Human review (controller steps in)")
        rt = Runtime(settings, now_provider=lambda: simulated_now(settings, 10))
        queue = rt.register.review_queue()
        console.print(f"{len(queue)} item(s) in the review queue:")
        for ln in queue:
            console.print(f"  * {ln.line_id} {ln.vendor_name}: "
                          f"{ln.hold_reason or ln.thread_status.value}")
        for ln in queue:
            if ln.status == AccrualStatus.HELD_FOR_REVIEW:
                note = "demo: variance reviewed and approved"
                line = rt.register.transition(
                    ln, AccrualStatus.CONFIRMED, actor="demo-controller",
                    source="review", hold_reason=None, notes=note,
                )
            elif ln.source_type == SourceType.SUNDRY_AGGREGATE:
                # The bulked sub-floor gaps: a human explicitly books the
                # estimate — one sundry JE, per-line gate intact.
                note = "demo: sundry sub-floor aggregate approved for posting"
                line = rt.register.transition(
                    ln, AccrualStatus.CONFIRMED, actor="demo-controller",
                    source="review", confirmed_source="human_estimate_approval",
                    notes=note,
                )
            else:
                continue
            je_id = rt.writeback.post_single(
                line, rt.calendar.period_by_name(line.period)
            )
            approvals.append({
                "line_id": line.line_id,
                "je_id": je_id,
                "vendor_name": line.vendor_name,
                "amount": str(line.postable_amount),
                "currency": line.currency,
                "actor": "demo-controller",
                "note": note,
            })
            console.print(
                f"  [green]approved[/green] {line.line_id} → JE {je_id} "
                f"({line.postable_amount:,.2f} {line.currency})"
            )

        banner("Final cycle after approvals")
        rt = Runtime(settings, now_provider=lambda: simulated_now(settings, 10))
        result = CloseCycleRunner(rt).run(close_day=10)
        summary(result)
        if on_snapshot:
            on_snapshot("final", 10, rt, result, approvals)

    return result
