"""JSON snapshot exporter for the static web demo UI.

Replays the scripted demo (``demo_runner.run_scripted_demo``) and captures
register state after each close day into a single ``demo-data.json`` the
Next.js app in ``web/`` imports at build time. Everything the frontend
renders — status labels, escalation labels, postable amounts — is resolved
here so the UI never re-implements Python business logic.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from ..config import Settings
from ..demo_runner import (
    DEMO_DAYS,
    final_narration_for,
    narration_for,
    run_scripted_demo,
    simulated_now,
)
from ..models import ESCALATION_LABELS, AccrualStatus, RunResult
from ..runtime import Runtime

FINAL_CLOSE_STEP = "final"


def snapshot_state(
    step_id: str,
    close_day: int,
    rt: Runtime,
    result: RunResult,
    approvals: list[dict],
) -> dict:
    period_name = result.period
    lines = rt.repo.lines(period=period_name)
    escalations = rt.repo.open_escalations()
    jes = rt.repo.journal_entries(period=period_name)
    held = [x for x in lines if x.status == AccrualStatus.HELD_FOR_REVIEW]
    unconfirmed = [x for x in lines if x.status == AccrualStatus.ESTIMATED]
    posted = [x for x in lines if x.status in (AccrualStatus.POSTED, AccrualStatus.CLEARED)]

    def line_dump(ln) -> dict:
        d = ln.model_dump(mode="json")
        d["postable_amount"] = str(ln.postable_amount)
        return d

    comms: dict[str, list[dict]] = {}
    for ln in rt.repo.lines():
        records = rt.repo.comms_for_line(ln.line_id)
        if records:
            comms[ln.line_id] = [c.model_dump(mode="json") for c in records]

    audit = [
        {
            "ts": str(r["ts"]),
            "line_id": r["line_id"],
            "actor": r["actor"],
            "source": r["source"],
            "field": r["field"],
            "old_value": r["old_value"],
            "new_value": r["new_value"],
        }
        for r in rt.repo.audit_rows(limit=200)
    ]

    run_result = result.model_dump(mode="json")
    run_result.pop("checkpoint_report_path", None)
    run_result.pop("dashboard_path", None)

    return {
        "id": step_id,
        "closeDay": close_day,
        "label": "Final" if step_id == FINAL_CLOSE_STEP else f"Day {close_day}",
        "date": simulated_now(rt.settings, close_day).date().isoformat(),
        "narration": (
            final_narration_for(rt.settings.profile) if step_id == FINAL_CLOSE_STEP
            else narration_for(rt.settings.profile)[close_day]
        ),
        "runResult": run_result,
        "kpis": {
            "lineCount": len(lines),
            "baseTotal": str(sum(x.base_amount for x in lines) or 0),
            "postedTotal": str(sum(x.base_amount for x in posted) or 0),
            "unconfirmed": len(unconfirmed),
            "held": len(held),
            "openEscalations": len(escalations),
            "posted": len(posted),
        },
        "lines": [line_dump(ln) for ln in lines],
        "journalEntries": [j.model_dump(mode="json") for j in jes],
        "escalations": [
            {**esc.model_dump(mode="json"), "label": ESCALATION_LABELS[esc.reason]}
            for esc in escalations
        ],
        "comms": comms,
        "audit": audit,
        "reviewQueue": [ln.line_id for ln in rt.register.review_queue()],
        "approvals": list(approvals),
        "trustLadder": [
            {
                "vendorId": s.vendor_id,
                "vendorName": s.vendor_name,
                "streak": s.streak,
                "required": s.required,
                "eligible": s.eligible,
                "revoked": s.revoked,
                "periods": [
                    {
                        "period": p.period,
                        "maxVariancePct": str(p.max_variance_pct),
                        "accurate": p.accurate,
                    }
                    for p in s.periods
                ],
            }
            for s in rt.trust.streaks()
        ],
    }


def export_demo_data(settings: Settings, console, out: Path) -> Path:
    steps: list[dict] = []

    def collect(step_id, close_day, rt, result, approvals):
        steps.append(snapshot_state(step_id, close_day, rt, result, approvals))

    run_scripted_demo(settings, console, on_snapshot=collect)

    expected = len(DEMO_DAYS) + 1
    if len(steps) != expected:
        raise RuntimeError(f"expected {expected} snapshots, captured {len(steps)}")

    payload = {
        "generatedAt": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "profile": settings.profile,
        "company": settings.effective_company_name,
        "period": steps[-1]["runResult"]["period"],
        "baseCurrency": settings.base_currency,
        "finalCloseDay": Runtime(settings).calendar.final_close_day,
        "steps": steps,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1))
    return out
