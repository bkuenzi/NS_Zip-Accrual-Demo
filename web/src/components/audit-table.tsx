"use client";

import { fmtDateTime } from "@/lib/format";
import type { AuditRow } from "@/lib/types";

export function AuditTable({ rows }: { rows: AuditRow[] }) {
  if (rows.length === 0)
    return <p className="text-xs text-muted">No audit entries.</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-border bg-background text-[10px] uppercase tracking-wide text-muted">
            <th className="px-3 py-2">when</th>
            <th className="px-3 py-2">line</th>
            <th className="px-3 py-2">actor</th>
            <th className="px-3 py-2">source</th>
            <th className="px-3 py-2">field</th>
            <th className="px-3 py-2">old → new</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-border last:border-b-0">
              <td className="whitespace-nowrap px-3 py-1.5 tabular-nums text-muted">
                {fmtDateTime(r.ts)}
              </td>
              <td className="whitespace-nowrap px-3 py-1.5">{r.line_id ?? "—"}</td>
              <td className="px-3 py-1.5">{r.actor}</td>
              <td className="px-3 py-1.5">{r.source}</td>
              <td className="px-3 py-1.5 font-medium">{r.field}</td>
              <td className="max-w-64 truncate px-3 py-1.5 text-muted">
                {(r.old_value ?? "∅") + " → " + (r.new_value ?? "∅")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
