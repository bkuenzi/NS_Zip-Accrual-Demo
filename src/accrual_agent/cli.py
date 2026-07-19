"""Command-line surface for the accrual agent.

Human-in-the-loop behavior is deliberately concise and status-first: what
needs attention leads, drill-down (register, threads, audit) is opt-in.
"""

from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import ConfigError, ContactRecord, get_settings
from .locking import LockBusyError, advisory_lock
from .logging_setup import configure_logging
from .models import AccrualStatus, ThreadStatus
from .register.service import current_actor
from .runtime import Runtime

app = typer.Typer(help="Autonomous accrual agent: NetSuite + Zip + vendor comms")
review_app = typer.Typer(help="Human review queue: held variances, close-risk estimates")
contacts_app = typer.Typer(help="Verified vendor contact management")
app.add_typer(review_app, name="review")
app.add_typer(contacts_app, name="contacts")

console = Console()


def _runtime(now: dt.datetime | None = None) -> Runtime:
    settings = get_settings()
    configure_logging(settings.output_dir)
    provider = (lambda: now) if now else None
    return Runtime(settings, now_provider=provider)


def _locked(settings, fn):
    try:
        with advisory_lock(settings.db_path):
            return fn()
    except LockBusyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None


def _closing_period(rt: Runtime):
    today = rt.now().astimezone(rt.calendar.tz).date()
    return rt.calendar.closing_period(today)


# ── cycle & stage commands ───────────────────────────────────────────────────


@app.command("run-cycle")
def run_cycle(
    close_day: int | None = typer.Option(None, help="Override the derived close day"),
    now: str | None = typer.Option(None, help="Simulate 'now' (ISO datetime, for testing)"),
):
    """Run the full daily close cycle (identify → confirm → comms → post → report)."""
    from .engine.close_cycle import CloseCycleRunner

    simulated = dt.datetime.fromisoformat(now) if now else None
    if simulated and simulated.tzinfo is None:
        simulated = simulated.replace(tzinfo=dt.UTC)
    rt = _runtime(simulated)

    def _run():
        result = CloseCycleRunner(rt).run(close_day=close_day)
        _print_run_summary(result)
        return result

    result = _locked(rt.settings, _run)
    raise typer.Exit(0 if result.ok else 1)


def _print_run_summary(result) -> None:
    console.print(
        f"[bold]{result.period} close, day {result.close_day}[/bold] — "
        f"+{result.lines_created} lines, {result.emails_sent} emails, "
        f"{result.replies_processed} replies, {result.jes_posted} JEs posted, "
        f"{result.lines_cleared} cleared, {result.escalations_raised} escalations"
    )
    for err in result.errors:
        console.print(f"  [red]stage {err.stage} failed:[/red] {err.error}")
    if result.dashboard_path:
        console.print(f"  dashboard: {result.dashboard_path}")
    if result.checkpoint_report_path:
        console.print(f"  report:    {result.checkpoint_report_path}")


@app.command("poll-inbox")
def poll_inbox():
    """Poll the accruals mailbox and apply vendor confirmations."""
    rt = _runtime()

    def _poll():
        processed = 0
        for line, parsed in rt.inbound.poll():
            flags = rt.confirmation.apply_reply(line, parsed)
            processed += 1
            refreshed = rt.repo.get_line(line.line_id)
            console.print(
                f"{line.line_id} {line.vendor_name}: "
                f"parsed {parsed.confirmed_amount} {parsed.currency or ''} "
                f"({parsed.method}, conf {parsed.confidence}) → "
                f"{refreshed.status.value if refreshed else '?'}"
            )
            for _, detail in flags:
                console.print(f"  [yellow]flag:[/yellow] {detail}")
        console.print(f"{processed} repl{'y' if processed == 1 else 'ies'} processed")

    _locked(rt.settings, _poll)


@app.command("send-requests")
def send_requests(close_day: int | None = typer.Option(None)):
    """Send due accrual-confirmation requests / reminders only."""
    rt = _runtime()
    day = close_day if close_day is not None else rt.calendar.close_day(
        rt.now().astimezone(rt.calendar.tz).date()
    )
    if hasattr(rt.mailer, "close_day"):
        rt.mailer.close_day = day

    def _send():
        sent, _ = rt.outbound.process(day)
        console.print(f"{sent} outbound message(s) processed (mode: "
                      f"{rt.settings.effective_outbound_mode})")

    _locked(rt.settings, _send)


@app.command("post-accruals")
def post_accruals(close_day: int | None = typer.Option(None)):
    """Post JEs for confirmed/auto-confirmed lines."""
    rt = _runtime()
    period = _closing_period(rt)
    day = close_day if close_day is not None else rt.calendar.close_day(
        rt.now().astimezone(rt.calendar.tz).date(), period
    )

    def _post():
        posted, flags = rt.writeback.post_eligible(period, day)
        console.print(f"{posted} JE(s) posted for {period.name}")
        for _, detail in flags:
            console.print(f"  [yellow]blocked:[/yellow] {detail}")

    _locked(rt.settings, _post)


@app.command("reverse-check")
def reverse_check():
    """Verify reversals: match arrived invoices to posted accruals, mark cleared."""
    rt = _runtime()
    period = _closing_period(rt)
    today = rt.now().astimezone(rt.calendar.tz).date()

    def _reconcile():
        cleared, flags = rt.writeback.reconcile(period, today)
        console.print(f"{cleared} accrual(s) cleared against invoices")
        for _, detail in flags:
            console.print(f"  [yellow]flag:[/yellow] {detail}")

    _locked(rt.settings, _reconcile)


# ── read-only views ──────────────────────────────────────────────────────────


@app.command()
def status(
    period: str | None = typer.Option(None, help="Period name, e.g. 2026-06"),
    full: bool = typer.Option(False, help="Show every line, not just attention items"),
):
    """Concise close status: what needs attention first."""
    rt = _runtime()
    period_name = period or _closing_period(rt).name
    lines = rt.repo.lines(period=period_name)
    if not lines:
        console.print(f"No accrual lines for {period_name}. Run `run-cycle` first.")
        return
    held = [x for x in lines if x.status == AccrualStatus.HELD_FOR_REVIEW]
    unconfirmed = [x for x in lines if x.status == AccrualStatus.ESTIMATED]
    escalations = rt.repo.open_escalations()
    base_total = sum((x.base_amount for x in lines), Decimal("0"))
    console.print(
        f"[bold]{period_name}[/bold]: {len(lines)} lines / "
        f"{base_total:,.2f} {rt.settings.base_currency} · "
        f"[red]{len(held)} held[/red] · [yellow]{len(unconfirmed)} unconfirmed[/yellow] · "
        f"{len(escalations)} open escalations"
    )
    show = lines if full else held + unconfirmed
    if not show:
        console.print("[green]Nothing needs attention.[/green]")
        return
    table = Table(show_lines=False)
    for col in ("line", "vendor", "amount", "status", "thread", "flags"):
        table.add_column(col)
    for ln in show:
        flags = []
        if ln.close_risk:
            flags.append("close-risk")
        if ln.gl_account is None:
            flags.append("no-GL")
        if ln.provisional:
            flags.append("provisional")
        thread = ("BLOCKED" if ln.thread_status == ThreadStatus.BLOCKED_NO_CONTACT
                  else ln.thread_status.value)
        table.add_row(
            ln.line_id, ln.vendor_name,
            f"{ln.postable_amount:,.2f} {ln.currency}",
            ln.status.value, thread, ", ".join(flags),
        )
    console.print(table)


@app.command()
def report():
    """Regenerate the exception report for the current closing period."""
    from .models import RunResult
    from .reporting.exception_report import build_report

    rt = _runtime()
    period = _closing_period(rt)
    day = rt.calendar.close_day(rt.now().astimezone(rt.calendar.tz).date(), period)
    text, _ = build_report(rt, period, day, RunResult(
        period=period.name, close_day=day, started_at=rt.now()
    ))
    console.print(text)


@app.command()
def dashboard():
    """Regenerate the HTML dashboard for the current closing period."""
    from .models import RunResult
    from .reporting.dashboard import write_dashboard

    rt = _runtime()
    period = _closing_period(rt)
    day = rt.calendar.close_day(rt.now().astimezone(rt.calendar.tz).date(), period)
    path = write_dashboard(rt, period, day, RunResult(
        period=period.name, close_day=day, started_at=rt.now()
    ))
    console.print(f"dashboard written: {path}")


# ── review queue ─────────────────────────────────────────────────────────────


@review_app.command("list")
def review_list():
    """Items awaiting human input: held variances, blocked sends, close risks."""
    rt = _runtime()
    queue = rt.register.review_queue()
    if not queue:
        console.print("[green]Review queue is empty.[/green]")
        return
    table = Table(title="Review queue")
    for col in ("line", "vendor", "estimate", "confirmed", "why"):
        table.add_column(col)
    for ln in queue:
        why = ln.hold_reason or (
            "blocked — no verified contact"
            if ln.thread_status == ThreadStatus.BLOCKED_NO_CONTACT
            else "no GL mapping" if ln.gl_account is None
            else "no subsidiary" if ln.subsidiary_id is None
            else "unconfirmed at close deadline" if ln.close_risk
            else "unconfirmed"
        )
        table.add_row(
            ln.line_id, ln.vendor_name,
            f"{ln.amount:,.2f} {ln.currency}",
            f"{ln.confirmed_amount:,.2f}" if ln.confirmed_amount is not None else "—",
            why,
        )
    console.print(table)
    console.print("approve: [bold]review approve <line>[/bold] · "
                  "reject: [bold]review reject <line> --note '...'[/bold]")


@review_app.command("approve")
def review_approve(
    line_id: str,
    as_user: str | None = typer.Option(None, "--as", help="Approver identity for the audit trail"),
    note: str | None = typer.Option(None, help="Optional approval note"),
):
    """Approve a held/close-risk line and post its JE to NetSuite immediately."""
    rt = _runtime()
    actor = current_actor(as_user)

    def _approve():
        line = rt.repo.get_line(line_id)
        if line is None:
            console.print(f"[red]unknown line {line_id}[/red]")
            raise typer.Exit(1)
        if line.status == AccrualStatus.HELD_FOR_REVIEW:
            line = rt.register.transition(
                line, AccrualStatus.CONFIRMED, actor=actor, source="review",
                hold_reason=None, notes=note or "approved from review queue",
            )
        elif line.status == AccrualStatus.ESTIMATED:
            # Close-endgame path: a human explicitly books the estimate.
            line = rt.register.transition(
                line, AccrualStatus.CONFIRMED, actor=actor, source="review",
                confirmed_source="human_estimate_approval",
                notes=note or "estimate approved for posting (unconfirmed by vendor)",
            )
        elif line.status in (AccrualStatus.CONFIRMED, AccrualStatus.AUTO_CONFIRMED):
            pass  # confirmed but blocked (e.g. GL was just mapped): try posting now
        else:
            console.print(f"[red]{line_id} is {line.status.value}; nothing to approve[/red]")
            raise typer.Exit(1)
        period = rt.calendar.period_by_name(line.period)
        try:
            je_id = rt.writeback.post_single(line, period)
        except RuntimeError as exc:
            console.print(f"[red]approved but cannot post:[/red] {exc}")
            raise typer.Exit(1) from None
        console.print(
            f"[green]{line_id} approved by {actor}[/green] — posted as NetSuite JE "
            f"[bold]{je_id}[/bold] ({line.postable_amount:,.2f} {line.currency})"
        )

    _locked(rt.settings, _approve)


@review_app.command("reject")
def review_reject(
    line_id: str,
    note: str = typer.Option(..., help="Reason for rejection (required)"),
    as_user: str | None = typer.Option(None, "--as"),
):
    """Reject a line: it will not accrue this period."""
    rt = _runtime()
    actor = current_actor(as_user)

    def _reject():
        line = rt.repo.get_line(line_id)
        if line is None:
            console.print(f"[red]unknown line {line_id}[/red]")
            raise typer.Exit(1)
        rt.register.transition(
            line, AccrualStatus.REJECTED, actor=actor, source="review",
            notes=f"rejected: {note}",
        )
        console.print(f"{line_id} rejected by {actor}: {note}")

    _locked(rt.settings, _reject)


# ── contacts ─────────────────────────────────────────────────────────────────


@contacts_app.command("add")
def contacts_add(
    vendor_id: str,
    email: str,
    name: str = typer.Option("", help="Contact name"),
    as_user: str | None = typer.Option(None, "--as"),
    force: bool = typer.Option(False, help="Skip the vendor-master domain cross-check"),
):
    """Add + verify a vendor contact (unblocks held outbound sends)."""
    rt = _runtime()
    actor = current_actor(as_user)
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        console.print(f"[red]{email} is not a valid email address[/red]")
        raise typer.Exit(1)
    domains = rt.vendor_domains.get(vendor_id, [])
    domain = email.rsplit("@", 1)[-1].lower()
    if domains and domain not in [d.lower() for d in domains] and not force:
        console.print(
            f"[red]domain {domain} does not match {vendor_id}'s vendor-master "
            f"domains {domains}[/red] (use --force to allowlist deliberately)"
        )
        raise typer.Exit(1)
    if force and domain not in [d.lower() for d in domains]:
        rt.contacts.allowed_domains.setdefault(vendor_id, []).append(domain)
    rt.contacts.upsert(ContactRecord(
        vendor_id=vendor_id, name=name, email=email, verified=True
    ))
    rt.repo.add_audit(None, actor, "contacts", f"contact:{vendor_id}", None,
                      f"{email} (verified)")
    console.print(f"[green]{vendor_id} contact {email} added and verified by {actor}[/green]")


@contacts_app.command("verify")
def contacts_verify(vendor_id: str, as_user: str | None = typer.Option(None, "--as")):
    """Mark an existing contact verified after the domain cross-check."""
    rt = _runtime()
    actor = current_actor(as_user)
    rec = rt.contacts.get(vendor_id)
    if rec is None:
        console.print(f"[red]no contact on file for {vendor_id}[/red]")
        raise typer.Exit(1)
    _, block = rt.contacts.verified_contact(vendor_id, rt.vendor_domains.get(vendor_id, []))
    if block and "not marked verified" not in block:
        console.print(f"[red]cannot verify: {block}[/red]")
        raise typer.Exit(1)
    rt.contacts.upsert(ContactRecord(
        vendor_id=vendor_id, name=rec.name, email=rec.email, verified=True
    ))
    rt.repo.add_audit(None, actor, "contacts", f"contact:{vendor_id}",
                      rec.email, f"{rec.email} (verified)")
    console.print(f"[green]{vendor_id} contact {rec.email} verified by {actor}[/green]")


# ── diagnostics & demo ───────────────────────────────────────────────────────


@app.command()
def doctor():
    """Check configuration and connectivity for every integration."""
    settings = get_settings()
    configure_logging(settings.output_dir, json_file=False)
    console.print(f"mode: [bold]{settings.mode}[/bold] · outbound: "
                  f"{settings.effective_outbound_mode} · base ccy: {settings.base_currency}")
    checks: list[tuple[str, str]] = []

    def check(label: str, fn):
        try:
            fn()
            checks.append((label, "[green]ok[/green]"))
        except Exception as exc:  # noqa: BLE001 — doctor reports, never crashes
            checks.append((label, f"[red]{exc}[/red]"))

    rt = Runtime(settings)
    check("templates", lambda: rt.templates)
    check("close calendar", lambda: rt.calendar)
    check("GL mappings", lambda: rt.gl_store)
    check("vendor contacts", lambda: rt.contacts)
    check("register db", lambda: rt.repo)
    if settings.mode == "live":
        check("netsuite", lambda: rt.adapters.netsuite.get_subsidiaries())
        check("zip", lambda: rt.adapters.zip.get_approved_requisitions(
            dt.date.today().replace(day=1), dt.date.today()))
        for platform in rt.adapters.ad_platforms:
            check(platform.platform_name, lambda p=platform: p.get_spend(
                dt.date.today() - dt.timedelta(days=2), dt.date.today() - dt.timedelta(days=1)
            ))
        check("smtp config", lambda: settings.require(
            {"SMTP_HOST": settings.smtp_host}, "SMTP"))
        check("imap config", lambda: settings.require(
            {"IMAP_HOST": settings.imap_host}, "IMAP"))
    else:
        check("mock adapters", lambda: rt.adapters.netsuite.get_vendors())
    for label, verdict in checks:
        console.print(f"  {label:<16} {verdict}")


@app.command()
def demo(
    keep: bool = typer.Option(False, help="Keep existing demo register instead of resetting"),
):
    """Scripted end-to-end walkthrough of the 2026-06 close in mock mode."""
    from .demo_runner import run_scripted_demo

    settings = get_settings()
    if settings.mode != "mock":
        console.print("[red]demo requires ACCRUAL_MODE=mock[/red]")
        raise typer.Exit(1)
    configure_logging(settings.output_dir)

    result = run_scripted_demo(
        settings, console, keep=keep, print_summary=_print_run_summary
    )

    console.print()
    console.print("[bold green]Demo complete.[/bold green] Inspect:")
    console.print(f"  * dashboard: {result.dashboard_path}")
    console.print(f"  * report:    {result.checkpoint_report_path}")
    console.print("  * register:  accrual-agent status --period 2026-06 --full")
    console.print("  * queue:     accrual-agent review list")


@app.command("export-web")
def export_web(
    out: str = typer.Option(
        "web/src/data/demo-data.json",
        help="Output path for the web demo data snapshot",
    ),
):
    """Run the scripted demo and export per-day JSON snapshots for the web UI."""
    from .reporting.web_export import export_demo_data

    settings = get_settings()
    if settings.mode != "mock":
        console.print("[red]export-web requires ACCRUAL_MODE=mock[/red]")
        raise typer.Exit(1)
    configure_logging(settings.output_dir)

    path = export_demo_data(settings, console, Path(out))
    console.print(f"[bold green]Web demo data written:[/bold green] {path}")


def main() -> None:
    try:
        app()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(2)


if __name__ == "__main__":
    main()
