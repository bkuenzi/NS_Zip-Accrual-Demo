"""Close-cycle orchestrator: the daily autonomous run.

Stage order (each isolated — one dead integration never stalls the close):
  1. identify        NetSuite + Zip uninvoiced gaps -> register
  2. api_accruals    ad-platform actuals -> auto-confirmed lines
  3. inbound         poll the accruals mailbox, apply vendor confirmations
  4. outbound        initial requests + escalating reminders (contact-gated)
  5. reconcile       arriving invoices confirm/clear accruals
  6. close_risk      flag unconfirmed threads near the deadline
  7. trust_ladder    final day: auto-post estimates of accuracy-streak vendors
  8. post            write eligible JEs back to NetSuite
  9. escalate        dedupe/dispatch team-lead escalations
 10. report          exception report + dashboard (pushed on checkpoint days)
"""

from __future__ import annotations

from ..logging_setup import get_logger
from ..models import EscalationReason, RunError, RunResult
from ..runtime import Runtime

log = get_logger(__name__)


class CloseCycleRunner:
    def __init__(self, runtime: Runtime):
        self.rt = runtime

    def run(self, close_day: int | None = None) -> RunResult:
        rt = self.rt
        now = rt.now()
        today = now.astimezone(rt.calendar.tz).date()
        period = rt.calendar.closing_period(today)
        day = close_day if close_day is not None else rt.calendar.close_day(today, period)
        if hasattr(rt.mailer, "close_day"):
            rt.mailer.close_day = day

        result = RunResult(
            period=period.name, close_day=day, started_at=now,
        )
        flags: list[tuple[EscalationReason, str]] = []
        log.info("cycle.start", period=period.name, close_day=day, mode=rt.settings.mode)

        def stage(name: str, fn) -> object | None:
            try:
                value = fn()
                result.stages_run.append(name)
                return value
            except Exception as exc:  # noqa: BLE001 — continue + collect by design
                log.error("cycle.stage_failed", stage=name, error=str(exc))
                result.errors.append(RunError(stage=name, error=str(exc)))
                return None

        if counts := stage("identify", lambda: rt.identification.run(period)):
            result.lines_created, result.lines_updated = counts

        if api := stage("api_accruals", lambda: rt.api_accruals.run(period, now)):
            touched, api_flags = api
            result.lines_updated += touched
            flags.extend(api_flags)

        def _inbound() -> int:
            processed = 0
            for line, parsed in rt.inbound.poll():
                flags.extend(rt.confirmation.apply_reply(line, parsed))
                processed += 1
            return processed

        if replies := stage("inbound", _inbound):
            result.replies_processed = replies

        if outbound := stage("outbound", lambda: rt.outbound.process(day)):
            sent, _ = outbound
            result.emails_sent = sent

        if reconciled := stage("reconcile", lambda: rt.writeback.reconcile(period, today)):
            cleared, rec_flags = reconciled
            result.lines_cleared = cleared
            flags.extend(rec_flags)

        stage("close_risk", lambda: rt.outbound.flag_close_risk(day))

        if day >= rt.calendar.final_close_day:
            stage("trust_ladder", lambda: rt.trust.promote(period.name))

        if posted := stage("post", lambda: rt.writeback.post_eligible(period, day)):
            count, post_flags = posted
            result.jes_posted = count
            flags.extend(post_flags)

        if dispatched := stage(
            "escalate", lambda: rt.escalation.process(flags, day, now)
        ):
            result.escalations_raised = dispatched

        def _report() -> tuple[str, str]:
            from ..reporting.dashboard import write_dashboard
            from ..reporting.exception_report import build_report, distribute_checkpoint

            report_text, needs_attention = build_report(rt, period, day, result)
            dashboard_path = write_dashboard(rt, period, day, result)
            is_checkpoint = day in rt.settings.checkpoint_days or (
                day >= rt.calendar.final_close_day
            )
            report_path = _write_report(rt, period, day, report_text)
            if is_checkpoint:
                distribute_checkpoint(
                    rt, period, day, report_text, needs_attention, dashboard_path
                )
            return report_path, dashboard_path

        if paths := stage("report", _report):
            result.checkpoint_report_path, result.dashboard_path = paths

        result.finished_at = rt.now()
        log.info(
            "cycle.done", period=period.name, close_day=day,
            created=result.lines_created, posted=result.jes_posted,
            errors=len(result.errors),
        )
        return result


def _write_report(rt: Runtime, period, day: int, text: str) -> str:
    from pathlib import Path

    out = Path(rt.settings.output_dir) / f"exception_report_{period.name}_day{day:02d}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return str(out)
