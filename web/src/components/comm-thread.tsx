"use client";

import { ArrowDownLeft, ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/cn";
import { fmtDateTime } from "@/lib/format";
import type { CommRecord } from "@/lib/types";

export function CommThread({ comms }: { comms: CommRecord[] }) {
  if (comms.length === 0)
    return <p className="text-xs text-muted">No vendor communications for this line.</p>;
  return (
    <ol className="flex flex-col gap-2">
      {comms.map((c) => {
        const inbound = c.direction === "inbound";
        return (
          <li
            key={`${c.comm_id}-${c.stage}`}
            className={cn(
              "max-w-[92%] rounded-xl border border-border px-3 py-2",
              inbound
                ? "self-start bg-background"
                : "self-end bg-accent/5 dark:bg-accent/10"
            )}
          >
            <div className="flex items-center gap-1.5 text-[11px] text-muted">
              {inbound ? (
                <ArrowDownLeft className="size-3 text-green-600 dark:text-green-400" />
              ) : (
                <ArrowUpRight className="size-3 text-accent" />
              )}
              <b className="text-foreground">
                {inbound ? c.sender : c.recipient}
              </b>
              · {c.stage} · {fmtDateTime(c.sent_at)} · {c.delivery}
            </div>
            <div className="mt-1 text-xs font-semibold">{c.subject}</div>
            {c.body_preview ? (
              <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap font-sans text-[11px] leading-relaxed text-muted">
                {c.body_preview}
              </pre>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
