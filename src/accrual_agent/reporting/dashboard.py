"""Static single-file HTML dashboard, regenerated every run.

Attention-first layout: close-day countdown + KPI tiles up top, then the
Needs Attention queue, then collapsible full register / comm threads / audit
trail sections. Self-contained (inline CSS, no external assets) so it can be
attached to an email or opened from a file share.
"""

from __future__ import annotations

import html
from decimal import Decimal
from pathlib import Path

from ..fiscal import Period
from ..models import ESCALATION_LABELS, AccrualStatus, RunResult, ThreadStatus
from ..runtime import Runtime

STATUS_BADGES = {
    AccrualStatus.ESTIMATED: ("estimated — pending confirmation", "#b45309", "#fef3c7"),
    AccrualStatus.CONFIRMED: ("confirmed", "#15803d", "#dcfce7"),
    AccrualStatus.AUTO_CONFIRMED: ("auto-confirmed", "#0e7490", "#cffafe"),
    AccrualStatus.HELD_FOR_REVIEW: ("held for review", "#b91c1c", "#fee2e2"),
    AccrualStatus.POSTED: ("posted", "#1d4ed8", "#dbeafe"),
    AccrualStatus.CLEARED: ("cleared", "#374151", "#e5e7eb"),
    AccrualStatus.REJECTED: ("rejected", "#6b7280", "#f3f4f6"),
}

CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
       background: #f8fafc; color: #0f172a; }
@media (prefers-color-scheme: dark) {
  body { background: #0b1120; color: #e2e8f0; }
  .card, details, table { background: #111a2e !important; }
  th { background: #1e293b !important; }
  tr:nth-child(even) td { background: #16203a; }
}
.wrap { max-width: 1100px; margin: 0 auto; padding: 24px 20px 60px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: #64748b; margin-bottom: 20px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 12px; margin-bottom: 24px; }
.card { background: #fff; border-radius: 10px; padding: 14px 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card .n { font-size: 24px; font-weight: 700; }
.card .l { color: #64748b; font-size: 12px; text-transform: uppercase;
           letter-spacing: .04em; }
.card.warn .n { color: #b91c1c; }
section h2 { font-size: 15px; margin: 26px 0 10px; }
table { width: 100%; border-collapse: collapse; background: #fff;
        border-radius: 10px; overflow: hidden;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }
th, td { text-align: left; padding: 8px 12px; font-size: 13px; }
th { background: #f1f5f9; color: #475569; font-size: 11px;
     text-transform: uppercase; letter-spacing: .05em; }
tr:nth-child(even) td { background: #fafcff; }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px;
         font-size: 11px; font-weight: 600; white-space: nowrap; }
details { background: #fff; border-radius: 10px; margin-top: 14px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }
details summary { cursor: pointer; padding: 12px 16px; font-weight: 600; }
details .inner { padding: 0 16px 16px; overflow-x: auto; }
.flag { color: #b91c1c; font-weight: 600; }
.small { color: #64748b; font-size: 12px; }
"""


def write_dashboard(
    rt: Runtime, period: Period, close_day: int, result: RunResult
) -> str:
    lines = rt.repo.lines(period=period.name)
    all_lines = rt.repo.lines()
    escalations = rt.repo.open_escalations()
    held = [x for x in lines if x.status == AccrualStatus.HELD_FOR_REVIEW]
    unconfirmed = [x for x in lines if x.status == AccrualStatus.ESTIMATED]
    posted = [x for x in lines if x.status in (AccrualStatus.POSTED, AccrualStatus.CLEARED)]
    base_total = sum((x.base_amount for x in lines), Decimal("0"))
    posted_total = sum((x.base_amount for x in posted), Decimal("0"))
    days_left = max(rt.calendar.final_close_day - close_day, 0)

    e = html.escape

    def badge(status: AccrualStatus) -> str:
        label, fg, bg = STATUS_BADGES[status]
        return (
            f'<span class="badge" style="color:{fg};background:{bg}">{label}</span>'
        )

    def money(amount: Decimal, currency: str) -> str:
        return f"{amount:,.2f}&nbsp;{currency}"

    def register_rows(rows) -> str:
        cells = []
        for ln in rows:
            thread = ln.thread_status.value.replace("_", " ")
            if ln.thread_status == ThreadStatus.BLOCKED_NO_CONTACT:
                thread = '<span class="flag">blocked — no contact</span>'
            flags = []
            if ln.close_risk:
                flags.append('<span class="flag">close risk</span>')
            if ln.gl_account is None:
                flags.append('<span class="flag">no GL</span>')
            if ln.provisional:
                flags.append('<span class="small">provisional</span>')
            cells.append(
                "<tr>"
                f"<td>{e(ln.line_id)}</td><td>{e(ln.vendor_name)}</td>"
                f"<td class='num'>{money(ln.postable_amount, ln.currency)}</td>"
                f"<td class='num'>{money(ln.base_amount, rt.settings.base_currency)}</td>"
                f"<td>{e(ln.gl_account or '—')} / {e(ln.cost_center or '—')}</td>"
                f"<td>{e(ln.subsidiary_id or '—')}</td>"
                f"<td>{e(ln.source_ref)}</td>"
                f"<td>{badge(ln.status)}</td>"
                f"<td>{thread} {' '.join(flags)}</td>"
                "</tr>"
            )
        return "".join(cells) or "<tr><td colspan='9'>none</td></tr>"

    attention_html = ""
    if held or unconfirmed or escalations or result.errors:
        items = []
        for err in result.errors:
            items.append(f"<li class='flag'>[{e(err.stage)}] {e(err.error)}</li>")
        for ln in held:
            items.append(
                f"<li><b>{e(ln.line_id)} {e(ln.vendor_name)}</b> — "
                f"{e(ln.hold_reason or 'held for review')} "
                f"<span class='small'>approve/reject via CLI review</span></li>"
            )
        for ln in unconfirmed:
            detail = {
                ThreadStatus.BLOCKED_NO_CONTACT: "outbound blocked — no verified contact",
                ThreadStatus.EXHAUSTED: "non-responsive after final reminder",
            }.get(ln.thread_status, "awaiting vendor confirmation")
            if ln.gl_account is None:
                detail += " · no GL mapping"
            items.append(
                f"<li><b>{e(ln.line_id)} {e(ln.vendor_name)}</b> "
                f"({money(ln.amount, ln.currency)}) — {e(detail)}</li>"
            )
        for esc in escalations:
            items.append(
                f"<li>[{e(esc.severity.upper())}] "
                f"{e(ESCALATION_LABELS[esc.reason])}"
                f"{' — ' + e(esc.line_id) if esc.line_id else ''}</li>"
            )
        attention_html = (
            "<section><h2>Needs attention</h2>"
            f"<div class='card'><ul>{''.join(items)}</ul></div></section>"
        )

    threads = []
    for ln in all_lines:
        comms = rt.repo.comms_for_line(ln.line_id)
        if not comms:
            continue
        rows = "".join(
            "<tr>"
            f"<td>{e(c.sent_at.isoformat()[:16] if c.sent_at else '')}</td>"
            f"<td>{e(c.direction)}</td><td>{e(c.stage)}</td>"
            f"<td>{e(c.recipient or c.sender or '')}</td>"
            f"<td>{e(c.subject[:80])}</td><td>{e(c.delivery)}</td>"
            "</tr>"
            for c in comms
        )
        threads.append(
            f"<h3 class='small'>{e(ln.line_id)} — {e(ln.vendor_name)}</h3>"
            "<table><tr><th>at</th><th>dir</th><th>stage</th><th>who</th>"
            f"<th>subject</th><th>delivery</th></tr>{rows}</table>"
        )

    audit_rows = "".join(
        "<tr>"
        f"<td>{e(str(r['ts'])[:19])}</td><td>{e(r['line_id'] or '—')}</td>"
        f"<td>{e(r['actor'])}</td><td>{e(r['source'])}</td><td>{e(r['field'])}</td>"
        f"<td>{e(str(r['old_value'] or ''))[:40]}</td>"
        f"<td>{e(str(r['new_value'] or ''))[:60]}</td>"
        "</tr>"
        for r in rt.repo.audit_rows(limit=150)
    )

    jes = rt.repo.journal_entries(period=period.name)
    je_rows = "".join(
        "<tr>"
        f"<td>{e(j.netsuite_id or '—')}</td><td>{e(j.line_id)}</td>"
        f"<td class='num'>{money(j.amount, j.currency)}</td>"
        f"<td>Dr {e(j.debit_account)} / Cr {e(j.credit_account)}</td>"
        f"<td>{e(j.tran_date.isoformat())}</td><td>{e(j.reversal_date.isoformat())}</td>"
        f"<td>{'yes' if j.estimate_based else ''}</td><td>{e(j.memo[:70])}</td>"
        "</tr>"
        for j in jes
    ) or "<tr><td colspan='8'>none</td></tr>"

    doc = f"""<meta charset="utf-8">
<title>Accrual close — {e(period.name)} day {close_day}</title>
<style>{CSS}</style>
<div class="wrap">
<h1>Accrual close — {e(period.name)}</h1>
<div class="sub">Day {close_day} of {rt.calendar.final_close_day}
 ({days_left} close day{'s' if days_left != 1 else ''} remaining) ·
 mode: {e(rt.settings.mode)} · generated by accrual-agent</div>
<div class="kpis">
  <div class="card"><div class="n">{len(lines)}</div><div class="l">register lines</div></div>
  <div class="card"><div class="n">{base_total:,.0f}</div>
    <div class="l">total accrual ({e(rt.settings.base_currency)})</div></div>
  <div class="card"><div class="n">{posted_total:,.0f}</div>
    <div class="l">posted ({e(rt.settings.base_currency)})</div></div>
  <div class="card {'warn' if unconfirmed else ''}"><div class="n">{len(unconfirmed)}</div>
    <div class="l">unconfirmed</div></div>
  <div class="card {'warn' if held else ''}"><div class="n">{len(held)}</div>
    <div class="l">held for review</div></div>
  <div class="card {'warn' if escalations else ''}"><div class="n">{len(escalations)}</div>
    <div class="l">open escalations</div></div>
</div>
{attention_html}
<section><h2>Accrual register — {e(period.name)}</h2>
<div style="overflow-x:auto"><table>
<tr><th>line</th><th>vendor</th><th>amount</th>
<th>base</th><th>GL / CC</th><th>sub</th><th>ref</th><th>status</th><th>thread</th></tr>
{register_rows(lines)}
</table></div></section>
<section><h2>Journal entries</h2>
<div style="overflow-x:auto"><table>
<tr><th>JE</th><th>line</th><th>amount</th><th>accounts</th><th>tran date</th>
<th>auto-reverses</th><th>estimate-based</th><th>memo</th></tr>
{je_rows}
</table></div></section>
<details><summary>Communication threads ({len(threads)})</summary>
<div class="inner">{''.join(threads) or 'none'}</div></details>
<details><summary>Audit trail (latest 150)</summary>
<div class="inner"><table>
<tr><th>ts</th><th>line</th><th>actor</th><th>source</th><th>field</th>
<th>old</th><th>new</th></tr>{audit_rows}</table></div></details>
</div>
"""
    out = Path(rt.settings.output_dir) / f"dashboard_{period.name}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)
    return str(out)
