"use client";

import Link from "next/link";
import { AlertTriangle, MailX, ShieldAlert } from "lucide-react";
import { useDemo } from "./demo-context";
import { Badge } from "./ui/badge";
import { Card, CardHeader } from "./ui/card";
import { SEVERITY_BADGE, THREAD_LABELS } from "@/lib/status";
import { money } from "@/lib/format";

export function AttentionList({ limit }: { limit?: number }) {
  const { step, raw } = useDemo();
  const held = step.lines.filter((l) => l.status === "held_for_review");
  const unconfirmed = step.lines.filter(
    (l) => l.status === "estimated_pending_confirmation"
  );

  type Item = { key: string; icon: React.ReactNode; body: React.ReactNode };
  const items: Item[] = [
    ...step.runResult.errors.map((err, i) => ({
      key: `err-${i}`,
      icon: <ShieldAlert className="size-4 shrink-0 text-red-500" />,
      body: (
        <span className="text-red-700 dark:text-red-300">
          [{err.stage}] {err.error}
        </span>
      ),
    })),
    ...held.map((l) => ({
      key: l.line_id,
      icon: <AlertTriangle className="size-4 shrink-0 text-red-500" />,
      body: (
        <span>
          <b>
            {l.line_id} {l.vendor_name}
          </b>{" "}
          — {l.hold_reason ?? "held for review"}
        </span>
      ),
    })),
    ...unconfirmed.map((l) => {
      const detail =
        l.thread_status === "blocked_no_contact"
          ? "outbound blocked — no verified contact"
          : l.thread_status === "exhausted"
            ? "non-responsive after final reminder"
            : "awaiting vendor confirmation";
      return {
        key: l.line_id,
        icon: <MailX className="size-4 shrink-0 text-amber-500" />,
        body: (
          <span>
            <b>
              {l.line_id} {l.vendor_name}
            </b>{" "}
            ({money(l.amount, l.currency)}) — {detail}
            {l.gl_account === null ? " · no GL mapping" : ""}
          </span>
        ),
      };
    }),
    ...step.escalations.map((e) => ({
      key: `esc-${e.escalation_id}`,
      icon: (
        <Badge className={SEVERITY_BADGE[e.severity]}>{e.severity}</Badge>
      ),
      body: (
        <span>
          {e.label}
          {e.line_id ? <span className="text-muted"> — {e.line_id}</span> : null}
        </span>
      ),
    })),
  ];

  const shown = limit ? items.slice(0, limit) : items;

  return (
    <Card>
      <CardHeader
        title="Needs attention"
        subtitle={`${items.length} open item${items.length === 1 ? "" : "s"} on ${raw.label.toLowerCase()}`}
        action={
          limit && items.length > shown.length ? (
            <Link
              href={{ pathname: "/attention", query: { day: raw.id } }}
              className="text-xs font-medium text-accent hover:underline"
            >
              View all →
            </Link>
          ) : undefined
        }
      />
      <ul className="px-5 pb-4">
        {shown.length === 0 && (
          <li className="py-2 text-sm text-muted">
            Nothing needs attention — the register is clean.
          </li>
        )}
        {shown.map((item) => (
          <li
            key={item.key}
            className="flex items-start gap-2.5 border-b border-border py-2 text-xs leading-relaxed last:border-b-0"
          >
            {item.icon}
            <div>{item.body}</div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function threadLabel(status: keyof typeof THREAD_LABELS): string {
  return THREAD_LABELS[status];
}
