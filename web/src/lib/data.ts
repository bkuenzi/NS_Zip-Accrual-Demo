import demoRaw from "@/data/demo-data.json";
import mvpRaw from "@/data/mvp-data.json";
import type { AccrualLine, AccrualStatus, DemoData, DemoStep, Kpis } from "./types";

export type DatasetKey = "demo" | "mvp";

export const datasets: Record<DatasetKey, DemoData> = {
  demo: demoRaw as unknown as DemoData,
  mvp: mvpRaw as unknown as DemoData,
};

export const DATASET_KEYS: DatasetKey[] = ["demo", "mvp"];

/** Static presentation copy for the launch chooser and header badge. */
export const DATASET_META: Record<
  DatasetKey,
  { label: string; company: string; tagline: string; blurb: string }
> = {
  demo: {
    label: "Demo walkthrough",
    company: "YourCo",
    tagline: "Guided sample dataset",
    blurb:
      "A compact, narrated 2026-06 close over seeded sample vendors — the fastest way to see every path the agent can take.",
  },
  mvp: {
    label: "MVP — SeatGeek dataset",
    company: "SeatGeek, Inc.",
    tagline: "Realistic NetSuite dataset",
    blurb:
      "The same close, run against the standalone SeatGeek accounting dataset: a NetSuite-shaped chart of accounts, real vendor archetypes (AWS, Google, Meta, Stripe, Snowflake…), Zip commitments, and ad-platform actuals.",
  },
};

export function isDatasetKey(value: string | null): value is DatasetKey {
  return value === "demo" || value === "mvp";
}

/** Company label for a dataset (falls back to the static meta). */
export function datasetCompany(key: DatasetKey): string {
  return datasets[key].company ?? DATASET_META[key].company;
}

const POSTED_STATUSES: AccrualStatus[] = ["posted", "cleared"];

export function isPosted(status: AccrualStatus): boolean {
  return POSTED_STATUSES.includes(status);
}

/** Lines whose status changed relative to the previous step (for row highlights). */
export function changedLineIds(
  steps: DemoStep[],
  index: number,
  lines: AccrualLine[]
): Set<string> {
  if (index <= 0) return new Set(lines.map((l) => l.line_id));
  const prev = new Map(steps[index - 1].lines.map((l) => [l.line_id, l.status]));
  return new Set(
    lines.filter((l) => prev.get(l.line_id) !== l.status).map((l) => l.line_id)
  );
}

/** Recompute KPI counts from a (possibly client-modified) set of lines. */
export function computeKpis(lines: AccrualLine[], openEscalations: number): Kpis {
  const posted = lines.filter((l) => isPosted(l.status));
  const sum = (xs: AccrualLine[]) =>
    xs.reduce((acc, l) => acc + Number(l.base_amount), 0).toFixed(2);
  return {
    lineCount: lines.length,
    baseTotal: sum(lines.filter((l) => l.status !== "rejected")),
    postedTotal: sum(posted),
    unconfirmed: lines.filter((l) => l.status === "estimated_pending_confirmation").length,
    held: lines.filter((l) => l.status === "held_for_review").length,
    openEscalations,
    posted: posted.length,
  };
}

export function stepByIndex(steps: DemoStep[], index: number): DemoStep {
  return steps[Math.min(Math.max(index, 0), steps.length - 1)];
}
