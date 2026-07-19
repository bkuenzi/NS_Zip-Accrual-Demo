"use client";

import { useDemo } from "./demo-context";
import { Card, CardHeader } from "./ui/card";
import { cn } from "@/lib/cn";
import { fmtDate, money } from "@/lib/format";

export function JeTable() {
  const { step } = useDemo();
  const jes = step.journalEntries;
  const total = jes.reduce((acc, j) => acc + Number(j.amount) * Number(j.exchange_rate), 0);

  return (
    <Card>
      <CardHeader
        title="Journal entries"
        subtitle={`${jes.length} auto-reversing accrual JEs posted to NetSuite · ${money(total)} base`}
      />
      <div className="overflow-x-auto px-2 pb-2">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-border text-[10px] uppercase tracking-wide text-muted">
              <th className="px-3 py-2">JE</th>
              <th className="px-3 py-2">line</th>
              <th className="px-3 py-2 text-right">amount</th>
              <th className="px-3 py-2">accounts</th>
              <th className="px-3 py-2">tran date</th>
              <th className="px-3 py-2">auto-reverses</th>
              <th className="px-3 py-2">basis</th>
              <th className="px-3 py-2">memo</th>
            </tr>
          </thead>
          <tbody>
            {jes.map((j) => (
              <tr key={j.je_id} className="border-b border-border last:border-b-0">
                <td className="whitespace-nowrap px-3 py-2.5 font-medium">
                  {j.netsuite_id ?? "—"}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5">{j.line_id}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums">
                  {money(j.amount, j.currency)}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 text-muted">
                  Dr {j.debit_account} / Cr {j.credit_account}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 tabular-nums">
                  {fmtDate(j.tran_date)}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 tabular-nums">
                  {fmtDate(j.reversal_date)}
                </td>
                <td
                  className={cn(
                    "whitespace-nowrap px-3 py-2.5",
                    j.estimate_based
                      ? "font-medium text-amber-700 dark:text-amber-400"
                      : "text-muted"
                  )}
                >
                  {j.estimate_based ? "estimate" : "confirmed"}
                </td>
                <td className="max-w-72 truncate px-3 py-2.5 text-muted" title={j.memo}>
                  {j.memo}
                </td>
              </tr>
            ))}
            {jes.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-muted">
                  Nothing posted yet — confirmations are still in flight.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
