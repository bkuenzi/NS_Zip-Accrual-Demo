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

from .config import Settings
from .locking import advisory_lock
from .models import AccrualStatus, RunResult
from .runtime import Runtime

DEMO_PERIOD_END = dt.date(2026, 6, 30)
DEMO_DAYS = (1, 3, 5, 7, 10)

NARRATION = {
    1: "Day 1 — identify uninvoiced spend; ad-platform actuals land as provisional "
       "auto-confirms; initial vendor requests go out (blocked where no contact).",
    3: "Day 3 — Acme confirms by email; first reminders fire for silent vendors; "
       "ad spend still inside the 72h settle window.",
    5: "Day 5 checkpoint — Zeta's German-format reply routes through the LLM "
       "fallback; Eta confirms; settled ad figures post; exception report goes out.",
    7: "Day 7 — Gamma disputes ($17.8k vs $15k) and Theta true-up breaches the "
       "±5% gate: both held for review; second reminders fire.",
    10: "Day 10 (final) — Beta exhausted the ladder: non-responsive escalation + "
        "close-risk flags; arriving invoices clear posted accruals.",
}

FINAL_NARRATION = (
    "Controller review — the held variances (Gamma dispute, Theta true-up) are "
    "reviewed and approved; their journal entries post and the close completes."
)

# MVP profile narration — same close mechanics against the SeatGeek dataset.
MVP_NARRATION = {
    1: "Day 1 — identify uninvoiced spend across NetSuite POs, Zip commitments, "
       "and ad platforms; Google/Meta actuals land as auto-confirms (Meta still "
       "provisional inside the 72h settle window); initial vendor requests go out "
       "(RXR blocked — no verified contact).",
    3: "Day 3 — AWS and The Trade Desk confirm by email; first reminders fire for "
       "silent vendors; Meta spend still inside the settle window.",
    5: "Day 5 checkpoint — Stormfactory's European-format reply routes through the "
       "LLM fallback; iHeart and impact.com confirm; settled ad figures post; "
       "exception report goes out.",
    7: "Day 7 — Snowflake's true-up ($212.4k vs $185k) breaches the ±5% gate and "
       "is held for review; Brooklyn Sports and Contentsquare confirm; second "
       "reminders fire.",
    10: "Day 10 (final) — Stripe exhausted the ladder: non-responsive escalation + "
        "close-risk; Apex Staffing has no GL mapping (unmapped escalation); "
        "arriving July invoices clear posted accruals.",
}

MVP_FINAL_NARRATION = (
    "Controller review — the held Snowflake true-up and any close-risk estimates "
    "are reviewed and approved; their journal entries post and the SeatGeek close "
    "completes."
)


def narration_for(profile: str) -> dict[int, str]:
    return MVP_NARRATION if profile == "mvp" else NARRATION


def final_narration_for(profile: str) -> str:
    return MVP_FINAL_NARRATION if profile == "mvp" else FINAL_NARRATION

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

    narration = narration_for(settings.profile)
    summary = print_summary or (lambda result: None)
    approvals: list[dict] = []

    with advisory_lock(settings.db_path):
        for day in DEMO_DAYS:
            banner(f"Close day {day} — {simulated_now(settings, day).date()}")
            console.print(narration[day], style="dim")
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
                line = rt.register.transition(
                    ln, AccrualStatus.CONFIRMED, actor="demo-controller",
                    source="review", hold_reason=None,
                    notes="demo: variance reviewed and approved",
                )
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
                    "note": "variance reviewed and approved",
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
