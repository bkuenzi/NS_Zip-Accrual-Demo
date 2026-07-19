"use client";

// The trust ladder: per-vendor estimate-vs-invoice accuracy streaks. A vendor
// whose cleared accruals stay inside the tolerance for the required number of
// consecutive periods earns estimate auto-posting on the final close day —
// revocable by the controller at any time.

import { ShieldCheck, TrendingUp } from "lucide-react";
import { useDemo } from "./demo-context";
import { Card, CardHeader } from "./ui/card";
import { cn } from "@/lib/cn";

export function TrustLadder() {
  const { step } = useDemo();
  const ladder = step.trustLadder ?? [];
  if (ladder.length === 0) return null;

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <TrendingUp className="size-4 text-cyan-600 dark:text-cyan-400" />
            Trust ladder — autonomy is earned
          </span>
        }
        subtitle="Consecutive periods where every cleared accrual landed within tolerance of the actual invoice. A full streak unlocks estimate auto-posting for that vendor; a miss resets it, and a controller can revoke it."
      />
      <ul className="px-5 pb-4">
        {ladder.map((v) => (
          <li
            key={v.vendorId}
            className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-2.5 last:border-b-0"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-sm font-medium">
                {v.vendorName}
                {v.eligible ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-cyan-100 px-2 py-0.5 text-[11px] font-semibold text-cyan-800 dark:bg-cyan-500/15 dark:text-cyan-300">
                    <ShieldCheck className="size-3" /> auto-post earned
                  </span>
                ) : null}
                {v.revoked ? (
                  <span className="rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-semibold text-red-800 dark:bg-red-500/15 dark:text-red-300">
                    revoked
                  </span>
                ) : null}
              </div>
              <p className="mt-0.5 text-xs text-muted">
                {v.periods
                  .map((p) => `${p.period}: ±${p.maxVariancePct}%`)
                  .join(" · ")}
              </p>
            </div>
            <div className="flex items-center gap-1.5" aria-label={`streak ${v.streak} of ${v.required}`}>
              {Array.from({ length: v.required }, (_, i) => (
                <span
                  key={i}
                  className={cn(
                    "size-2.5 rounded-full",
                    i < v.streak
                      ? "bg-cyan-500"
                      : "bg-slate-200 dark:bg-slate-600/50"
                  )}
                />
              ))}
              <span className="ml-1 text-xs font-medium text-muted">
                {v.streak}/{v.required}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
