"use client";

import { useEffect, useRef, useState } from "react";
import { useDemo } from "./demo-context";
import { Card } from "./ui/card";
import { cn } from "@/lib/cn";
import { moneyCompact } from "@/lib/format";

/** Animate a numeric value toward its target on step changes. */
function useCountUp(target: number, ms = 500): number {
  const [value, setValue] = useState(target);
  const fromRef = useRef(target);
  useEffect(() => {
    const from = fromRef.current;
    if (from === target) return;
    const start = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min((t - start) / ms, 1);
      const eased = 1 - (1 - p) ** 3;
      setValue(from + (target - from) * eased);
      if (p < 1) raf = requestAnimationFrame(tick);
      else fromRef.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return value;
}

function Kpi({
  label,
  value,
  display,
  warn,
  sub,
}: {
  label: string;
  value: number;
  display?: (v: number) => string;
  warn?: boolean;
  sub?: string;
}) {
  const v = useCountUp(value);
  return (
    <Card className="px-4 py-3">
      <div
        className={cn(
          "text-2xl font-bold tracking-tight",
          warn && value > 0 && "text-red-600 dark:text-red-400"
        )}
      >
        {display ? display(v) : Math.round(v).toLocaleString("en-US")}
      </div>
      <div className="mt-0.5 text-[11px] font-medium uppercase tracking-wide text-muted">
        {label}
      </div>
      {sub ? <div className="mt-0.5 text-[11px] text-muted">{sub}</div> : null}
    </Card>
  );
}

export function KpiCards() {
  const { step, raw, data } = useDemo();
  const k = step.kpis;
  const daysLeft = Math.max(data.finalCloseDay - raw.closeDay, 0);
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <Kpi label="register lines" value={k.lineCount} />
      <Kpi
        label={`total accrual (${data.baseCurrency})`}
        value={Number(k.baseTotal)}
        display={moneyCompact}
      />
      <Kpi
        label={`posted (${data.baseCurrency})`}
        value={Number(k.postedTotal)}
        display={moneyCompact}
      />
      <Kpi label="unconfirmed" value={k.unconfirmed} warn />
      <Kpi label="held for review" value={k.held} warn />
      <Kpi
        label="open escalations"
        value={k.openEscalations}
        warn
        sub={daysLeft > 0 ? `${daysLeft} close day${daysLeft === 1 ? "" : "s"} left` : "close complete"}
      />
    </div>
  );
}
