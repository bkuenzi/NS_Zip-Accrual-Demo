"use client";

// The interactive controller-review step. On day 10 the held-for-review
// variances await a decision: approving a line promotes it to its real
// final-step state (posted, JE included) — no invented data.

import { Check, ClipboardCheck, X } from "lucide-react";
import { useDemo } from "./demo-context";
import { Button } from "./ui/button";
import { Card, CardHeader } from "./ui/card";
import { StatusBadge } from "./ui/badge";
import { money } from "@/lib/format";

export function ReviewQueue() {
  const { raw, step, isReviewStep, decisions, decide } = useDemo();
  if (!isReviewStep) return null;

  const held = raw.lines.filter((l) => l.status === "held_for_review");
  if (held.length === 0) return null;

  return (
    <Card className="border-red-200 dark:border-red-500/30">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <ClipboardCheck className="size-4 text-red-500" />
            Controller review — your decision needed
          </span>
        }
        subtitle="These accruals breached the variance gate and will not post without a human decision. Approve to post the vendor-confirmed amount; reject to drop the accrual."
      />
      <ul className="px-5 pb-4">
        {held.map((l) => {
          const decision = decisions[l.line_id];
          const current = step.lines.find((x) => x.line_id === l.line_id) ?? l;
          return (
            <li
              key={l.line_id}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-3 last:border-b-0"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                  {l.line_id} · {l.vendor_name}
                  <StatusBadge status={current.status} />
                </div>
                <p className="mt-1 max-w-xl text-xs leading-relaxed text-muted">
                  {l.hold_reason}
                </p>
                <p className="mt-0.5 text-xs text-muted">
                  estimate {money(l.amount, l.currency)} → vendor confirmed{" "}
                  <b className="text-foreground">
                    {money(l.confirmed_amount ?? l.amount, l.currency)}
                  </b>
                  {l.invoice_number ? ` · invoice ${l.invoice_number}` : ""}
                </p>
              </div>
              {decision ? (
                <span className="text-xs font-medium text-muted">
                  {decision === "approved" ? (
                    <span className="inline-flex items-center gap-1 text-green-700 dark:text-green-400">
                      <Check className="size-3.5" /> approved — JE posted
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1">
                      <X className="size-3.5" /> rejected — accrual dropped
                    </span>
                  )}
                </span>
              ) : (
                <div className="flex gap-2">
                  <Button
                    variant="success"
                    onClick={() => decide(l.line_id, "approved")}
                  >
                    <Check className="size-3.5" /> Approve
                  </Button>
                  <Button
                    variant="danger"
                    onClick={() => decide(l.line_id, "rejected")}
                  >
                    <X className="size-3.5" /> Reject
                  </Button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
