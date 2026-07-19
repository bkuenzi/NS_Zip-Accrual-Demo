"use client";

// Register composition by status: a single stacked horizontal bar with 2px
// gaps, direct labels on every visible segment, and a full legend below —
// color never carries meaning alone (dataviz status-palette rule).

import { useTheme } from "next-themes";
import { useDemo } from "./../demo-context";
import { Card, CardHeader } from "../ui/card";
import { CHART_STATUS_ORDER, STATUS_COLORS, STATUS_META } from "@/lib/status";
import { useMounted } from "@/lib/use-mounted";

export function StatusBar() {
  const { step } = useDemo();
  const { resolvedTheme } = useTheme();
  const mounted = useMounted();
  const mode = mounted && resolvedTheme === "dark" ? "dark" : "light";

  const counts = CHART_STATUS_ORDER.map((status) => ({
    status,
    count: step.lines.filter((l) => l.status === status).length,
  }));
  const total = step.lines.length || 1;
  const visible = counts.filter((c) => c.count > 0);

  return (
    <Card>
      <CardHeader
        title="Register by status"
        subtitle={`${step.lines.length} accrual lines · ${step.label}`}
      />
      <div className="px-5 pb-4">
        <div className="flex h-8 w-full gap-0.5 overflow-hidden rounded-lg">
          {visible.map(({ status, count }) => (
            <div
              key={status}
              title={`${STATUS_META[status].label}: ${count}`}
              style={{
                width: `${(count / total) * 100}%`,
                background: STATUS_COLORS[status][mode],
              }}
              className="flex min-w-6 items-center justify-center rounded-[3px] text-[11px] font-bold text-white transition-[width] duration-500"
            >
              {count}
            </div>
          ))}
        </div>
        <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
          {counts.map(({ status, count }) => (
            <li
              key={status}
              className="flex items-center gap-1.5 text-xs text-muted"
            >
              <span
                className="size-2.5 rounded-[3px]"
                style={{ background: STATUS_COLORS[status][mode] }}
              />
              {STATUS_META[status].label}
              <span className="font-semibold text-foreground">{count}</span>
            </li>
          ))}
        </ul>
      </div>
    </Card>
  );
}
