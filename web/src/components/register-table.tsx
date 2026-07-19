"use client";

import { ArrowUpDown, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useDemo } from "./demo-context";
import { LineDetailSheet } from "./line-detail-sheet";
import { Card, CardHeader } from "./ui/card";
import { StatusBadge } from "./ui/badge";
import { cn } from "@/lib/cn";
import { money } from "@/lib/format";
import { SOURCE_LABELS, STATUS_META, THREAD_LABELS } from "@/lib/status";
import type { AccrualLine, AccrualStatus } from "@/lib/types";

type SortKey = "line_id" | "vendor_name" | "base_amount" | "status";

function Th({
  label,
  k,
  className,
  sortKey,
  onSort,
}: {
  label: string;
  k?: SortKey;
  className?: string;
  sortKey: SortKey;
  onSort: (k: SortKey) => void;
}) {
  return (
    <th className={cn("px-3 py-2", className)}>
      {k ? (
        <button
          onClick={() => onSort(k)}
          className={cn(
            "inline-flex cursor-pointer items-center gap-1 uppercase tracking-wide hover:text-foreground",
            sortKey === k && "text-foreground"
          )}
        >
          {label} <ArrowUpDown className="size-3" />
        </button>
      ) : (
        <span className="uppercase tracking-wide">{label}</span>
      )}
    </th>
  );
}

export function RegisterTable() {
  const { step, changed } = useDemo();
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<AccrualStatus | "all">("all");
  const [sortKey, setSortKey] = useState<SortKey>("line_id");
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const [selected, setSelected] = useState<AccrualLine | null>(null);

  const presentStatuses = useMemo(
    () => [...new Set(step.lines.map((l) => l.status))],
    [step.lines]
  );

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = step.lines.filter((l) => {
      if (statusFilter !== "all" && l.status !== statusFilter) return false;
      if (!q) return true;
      return [l.line_id, l.vendor_name, l.source_ref, l.gl_account ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(q);
    });
    return [...filtered].sort((a, b) => {
      const va = sortKey === "base_amount" ? Number(a[sortKey]) : a[sortKey];
      const vb = sortKey === "base_amount" ? Number(b[sortKey]) : b[sortKey];
      return (va < vb ? -1 : va > vb ? 1 : 0) * sortDir;
    });
  }, [step.lines, query, statusFilter, sortKey, sortDir]);

  const sortBy = (key: SortKey) => {
    if (key === sortKey) setSortDir((d) => (d === 1 ? -1 : 1));
    else {
      setSortKey(key);
      setSortDir(1);
    }
  };

  return (
    <Card>
      <CardHeader
        title={`Accrual register — ${step.lines[0]?.period ?? ""}`}
        subtitle={`${rows.length} of ${step.lines.length} lines · click a row for the full story`}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <label className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search vendor, line, PO…"
                className="w-44 rounded-lg border border-border bg-background py-1.5 pl-7 pr-2 text-xs outline-none placeholder:text-muted focus:border-accent"
              />
            </label>
            <select
              value={statusFilter}
              onChange={(e) =>
                setStatusFilter(e.target.value as AccrualStatus | "all")
              }
              className="rounded-lg border border-border bg-background px-2 py-1.5 text-xs outline-none focus:border-accent"
            >
              <option value="all">all statuses</option>
              {presentStatuses.map((s) => (
                <option key={s} value={s}>
                  {STATUS_META[s].label}
                </option>
              ))}
            </select>
          </div>
        }
      />
      <div className="overflow-x-auto px-2 pb-2">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-border text-[10px] text-muted">
              <Th label="line" k="line_id" sortKey={sortKey} onSort={sortBy} />
              <Th label="vendor" k="vendor_name" sortKey={sortKey} onSort={sortBy} />
              <Th label="amount" className="text-right" sortKey={sortKey} onSort={sortBy} />
              <Th label="base" k="base_amount" className="text-right" sortKey={sortKey} onSort={sortBy} />
              <Th label="GL / CC" sortKey={sortKey} onSort={sortBy} />
              <Th label="source" sortKey={sortKey} onSort={sortBy} />
              <Th label="status" k="status" sortKey={sortKey} onSort={sortBy} />
              <Th label="thread" sortKey={sortKey} onSort={sortBy} />
            </tr>
          </thead>
          <tbody>
            {rows.map((l) => (
              <tr
                key={l.line_id}
                onClick={() => setSelected(l)}
                className={cn(
                  "cursor-pointer border-b border-border transition-colors last:border-b-0 hover:bg-background",
                  changed.has(l.line_id) && "row-changed"
                )}
              >
                <td className="whitespace-nowrap px-3 py-2.5 font-medium">
                  {l.line_id}
                </td>
                <td className="px-3 py-2.5">{l.vendor_name}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums">
                  {money(l.postable_amount, l.currency)}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums">
                  {money(l.base_amount)}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 text-muted">
                  {l.gl_account ?? "—"} / {l.cost_center ?? "—"}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 text-muted">
                  {SOURCE_LABELS[l.source_type]}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5">
                  <StatusBadge status={l.status} />
                </td>
                <td className="whitespace-nowrap px-3 py-2.5">
                  <span
                    className={cn(
                      l.thread_status === "blocked_no_contact" &&
                        "font-semibold text-red-600 dark:text-red-400",
                      l.thread_status === "exhausted" &&
                        "font-semibold text-red-600 dark:text-red-400"
                    )}
                  >
                    {THREAD_LABELS[l.thread_status]}
                  </span>
                  {l.close_risk && (
                    <span className="ml-1.5 font-semibold text-red-600 dark:text-red-400">
                      · close risk
                    </span>
                  )}
                  {l.provisional && (
                    <span className="ml-1.5 text-muted">· provisional</span>
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-muted">
                  No lines match the current filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <LineDetailSheet line={selected} onClose={() => setSelected(null)} />
    </Card>
  );
}
