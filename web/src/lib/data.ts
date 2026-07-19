import raw from "@/data/demo-data.json";
import type { AccrualLine, AccrualStatus, DemoData, DemoStep, Kpis } from "./types";

export const demoData = raw as unknown as DemoData;

export const steps = demoData.steps;
export const finalStep = steps[steps.length - 1];
export const day10Index = steps.findIndex((s) => s.id === "day-10");
export const finalIndex = steps.length - 1;

const POSTED_STATUSES: AccrualStatus[] = ["posted", "cleared"];

export function isPosted(status: AccrualStatus): boolean {
  return POSTED_STATUSES.includes(status);
}

/** Lines whose status changed relative to the previous step (for row highlights). */
export function changedLineIds(index: number, lines: AccrualLine[]): Set<string> {
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

export function stepByIndex(index: number): DemoStep {
  return steps[Math.min(Math.max(index, 0), steps.length - 1)];
}
