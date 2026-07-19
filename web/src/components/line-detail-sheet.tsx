"use client";

import { useDemo } from "./demo-context";
import { AuditTable } from "./audit-table";
import { CommThread } from "./comm-thread";
import { Sheet } from "./ui/sheet";
import { StatusBadge } from "./ui/badge";
import { SOURCE_LABELS, THREAD_LABELS } from "@/lib/status";
import { fmtDate, money } from "@/lib/format";
import type { AccrualLine } from "@/lib/types";

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[10px] font-medium uppercase tracking-wide text-muted">
        {label}
      </dt>
      <dd className="mt-0.5 text-xs">{value}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-5 first:mt-0">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
        {title}
      </h3>
      {children}
    </section>
  );
}

export function LineDetailSheet({
  line,
  onClose,
}: {
  line: AccrualLine | null;
  onClose: () => void;
}) {
  const { step } = useDemo();
  if (!line) return null;

  const comms = step.comms[line.line_id] ?? [];
  const audit = step.audit.filter((r) => r.line_id === line.line_id);
  const jes = step.journalEntries.filter((j) => j.line_id === line.line_id);

  return (
    <Sheet
      open
      onClose={onClose}
      title={
        <span className="flex items-center gap-2">
          {line.line_id} · {line.vendor_name}
          <StatusBadge status={line.status} />
        </span>
      }
      subtitle={`${SOURCE_LABELS[line.source_type]} · ${line.source_ref} · ${line.period}`}
    >
      <Section title="Accrual">
        <p className="mb-3 rounded-lg bg-background px-3 py-2 text-xs leading-relaxed text-muted">
          {line.estimate_basis}
        </p>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
          <Fact label="Estimate" value={money(line.amount, line.currency)} />
          <Fact
            label="Confirmed"
            value={
              line.confirmed_amount
                ? `${money(line.confirmed_amount, line.currency)} (${line.confirmed_source})`
                : "—"
            }
          />
          <Fact
            label={`Base (${line.exchange_rate} rate)`}
            value={money(line.base_amount)}
          />
          <Fact label="GL / cost center" value={`${line.gl_account ?? "—"} / ${line.cost_center ?? "—"}`} />
          <Fact label="Subsidiary" value={line.subsidiary_id ?? "—"} />
          <Fact label="Thread" value={THREAD_LABELS[line.thread_status]} />
          <Fact
            label="Invoice"
            value={
              line.invoice_number
                ? `${line.invoice_number} · ETA ${fmtDate(line.invoice_eta)}`
                : "—"
            }
          />
          <Fact label="Provisional" value={line.provisional ? "yes — inside settle window" : "no"} />
          <Fact label="Close risk" value={line.close_risk ? "yes" : "no"} />
        </dl>
        {line.hold_reason ? (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs leading-relaxed text-red-700 dark:bg-red-500/10 dark:text-red-300">
            {line.hold_reason}
          </p>
        ) : null}
        {line.notes ? (
          <p className="mt-2 text-xs text-muted">note: {line.notes}</p>
        ) : null}
      </Section>

      {jes.length > 0 && (
        <Section title={`Journal entries (${jes.length})`}>
          <ul className="flex flex-col gap-2">
            {jes.map((j) => (
              <li
                key={j.je_id}
                className="rounded-lg border border-border px-3 py-2 text-xs"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <b>{j.netsuite_id ?? "pending"}</b>
                  <span className="tabular-nums">{money(j.amount, j.currency)}</span>
                </div>
                <div className="mt-0.5 text-muted">
                  Dr {j.debit_account} / Cr {j.credit_account} · posted{" "}
                  {fmtDate(j.tran_date)} · auto-reverses {fmtDate(j.reversal_date)}
                  {j.estimate_based ? " · estimate-based" : ""}
                </div>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title={`Vendor thread (${comms.length})`}>
        <CommThread comms={comms} />
      </Section>

      <Section title={`Audit trail (${audit.length})`}>
        <AuditTable rows={audit} />
      </Section>
    </Sheet>
  );
}
