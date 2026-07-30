"use client";

// Accrued vs posted (base currency) across the close: two-series line chart,
// one axis, legend + direct hover tooltip, current step marked.

import { useTheme } from "next-themes";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useDemo } from "./../demo-context";
import { Card, CardHeader } from "../ui/card";
import { money, moneyCompact } from "@/lib/format";
import { useMounted } from "@/lib/use-mounted";

const SERIES = {
  total: { label: "Total accrual", light: "#475569", dark: "#94a3b8" },
  posted: { label: "Posted", light: "#1d4ed8", dark: "#60a5fa" },
};

export function CloseTrend() {
  const { raw, steps, data: dataset } = useDemo();
  const data = steps.map((s) => ({
    label: s.label,
    total: Number(s.kpis.baseTotal),
    posted: Number(s.kpis.postedTotal),
  }));
  const { resolvedTheme } = useTheme();
  const mounted = useMounted();
  const mode = mounted && resolvedTheme === "dark" ? "dark" : "light";
  const grid = mode === "dark" ? "#1e293b" : "#e2e8f0";
  const ink = mode === "dark" ? "#94a3b8" : "#64748b";

  return (
    <Card>
      <CardHeader
        title={`Accrued vs posted (${dataset.baseCurrency})`}
        subtitle="Base-currency totals across the close cycle"
        action={
          <ul className="flex gap-3 pt-0.5">
            {Object.values(SERIES).map((s) => (
              <li key={s.label} className="flex items-center gap-1.5 text-xs text-muted">
                <span
                  className="h-0.5 w-4 rounded-full"
                  style={{ background: s[mode] }}
                />
                {s.label}
              </li>
            ))}
          </ul>
        }
      />
      <div className="h-56 px-2 pb-3">
        {mounted && (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 16, left: 4, bottom: 0 }}>
              <CartesianGrid stroke={grid} strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fill: ink, fontSize: 11 }}
                axisLine={{ stroke: grid }}
                tickLine={false}
              />
              <YAxis
                tickFormatter={(v: number) => moneyCompact(v)}
                tick={{ fill: ink, fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={44}
              />
              <Tooltip
                cursor={{ stroke: grid }}
                contentStyle={{
                  background: mode === "dark" ? "#111a2e" : "#ffffff",
                  border: `1px solid ${grid}`,
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(value, name) => [
                  money(Number(value), dataset.baseCurrency),
                  name === "total" ? SERIES.total.label : SERIES.posted.label,
                ]}
              />
              <ReferenceLine x={raw.label} stroke={ink} strokeDasharray="4 4" />
              <Line
                type="monotone"
                dataKey="total"
                stroke={SERIES.total[mode]}
                strokeWidth={2}
                dot={{ r: 3, fill: SERIES.total[mode], strokeWidth: 0 }}
                activeDot={{ r: 5 }}
              />
              <Line
                type="monotone"
                dataKey="posted"
                stroke={SERIES.posted[mode]}
                strokeWidth={2}
                dot={{ r: 3, fill: SERIES.posted[mode], strokeWidth: 0 }}
                activeDot={{ r: 5 }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}
