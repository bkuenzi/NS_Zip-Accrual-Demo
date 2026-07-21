"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";
import {
  changedLineIds,
  computeKpis,
  datasets,
  type DatasetKey,
} from "@/lib/data";
import type { AccrualLine, DemoData, DemoStep, JournalEntry } from "@/lib/types";

export type ReviewDecision = "approved" | "rejected";

/** Lines the day-10 controller decides on: held variances plus the
 *  estimate-based sundry aggregate awaiting explicit approval. */
export function isReviewable(l: AccrualLine): boolean {
  return (
    l.status === "held_for_review" ||
    (l.source_type === "sundry_aggregate" &&
      l.status === "estimated_pending_confirmation")
  );
}

interface DemoContextValue {
  /** Which dataset is active (demo | mvp). */
  datasetKey: DatasetKey;
  /** The whole active dataset (period, company, finalCloseDay, …). */
  data: DemoData;
  /** Steps for the active dataset. */
  steps: DemoStep[];
  /** Index into steps[] (0..5). */
  index: number;
  setIndex: (i: number) => void;
  /** The raw exported snapshot for the current step. */
  raw: DemoStep;
  /** Snapshot with client-side review decisions overlaid (day 10 only). */
  step: DemoStep;
  /** Line ids whose status changed vs. the previous step. */
  changed: Set<string>;
  /** Review decisions made by the viewer on day 10. */
  decisions: Record<string, ReviewDecision>;
  decide: (lineId: string, decision: ReviewDecision) => void;
  resetReview: () => void;
  /** True when the current step is day 10 (the interactive review step). */
  isReviewStep: boolean;
  /** Held lines on day 10 still awaiting a decision. */
  pendingReview: string[];
}

const DemoContext = createContext<DemoContextValue | null>(null);

/** Overlay the viewer's approve/reject decisions onto the day-10 snapshot.
 *  Approved lines are promoted to their final-step version (real exported
 *  data — status posted, JE included); rejected lines get status "rejected". */
function applyDecisions(
  raw: DemoStep,
  decisions: Record<string, ReviewDecision>,
  finalStep: DemoStep
): DemoStep {
  const decided = Object.keys(decisions);
  if (raw.id !== "day-10" || decided.length === 0) return raw;

  const finalLines = new Map(finalStep.lines.map((l) => [l.line_id, l]));
  const lines = raw.lines.map((l) => {
    const d = decisions[l.line_id];
    if (!d || !isReviewable(l)) return l;
    if (d === "rejected") return { ...l, status: "rejected" as const };
    return finalLines.get(l.line_id) ?? l;
  });

  const approvedIds = decided.filter((id) => decisions[id] === "approved");
  const existing = new Set(raw.journalEntries.map((j) => j.je_id));
  const promotedJes: JournalEntry[] = finalStep.journalEntries.filter(
    (j) => approvedIds.includes(j.line_id) && !existing.has(j.je_id)
  );

  const escalations = raw.escalations.filter(
    (e) =>
      !(
        e.line_id &&
        decisions[e.line_id] &&
        (e.reason === "variance_breach" || e.reason === "close_risk")
      )
  );

  return {
    ...raw,
    lines,
    journalEntries: [...raw.journalEntries, ...promotedJes],
    escalations,
    reviewQueue: raw.reviewQueue.filter((id) => !decisions[id]),
    kpis: computeKpis(lines, escalations.length),
  };
}

export function DemoProvider({
  datasetKey,
  children,
}: {
  datasetKey: DatasetKey;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const data = datasets[datasetKey];
  const steps = data.steps;
  const finalStep = steps[steps.length - 1];

  const fromUrl = steps.findIndex((s) => s.id === params.get("day"));
  const index = fromUrl >= 0 ? fromUrl : 0;

  const setIndex = useCallback(
    (i: number) => {
      const clamped = Math.min(Math.max(i, 0), steps.length - 1);
      const q = new URLSearchParams(params.toString());
      q.set("day", steps[clamped].id);
      router.replace(`${pathname}?${q.toString()}`, { scroll: false });
    },
    [params, pathname, router, steps]
  );

  const [decisions, setDecisions] = useState<Record<string, ReviewDecision>>({});

  const decide = useCallback((lineId: string, decision: ReviewDecision) => {
    setDecisions((d) => ({ ...d, [lineId]: decision }));
  }, []);
  const resetReview = useCallback(() => setDecisions({}), []);

  const raw = steps[index];
  const step = useMemo(
    () => applyDecisions(raw, decisions, finalStep),
    [raw, decisions, finalStep]
  );
  const changed = useMemo(
    () => changedLineIds(steps, index, raw.lines),
    [steps, index, raw]
  );

  const pendingReview = useMemo(
    () =>
      raw.id === "day-10"
        ? raw.lines
            .filter((l) => isReviewable(l) && !decisions[l.line_id])
            .map((l) => l.line_id)
        : [],
    [raw, decisions]
  );

  const value: DemoContextValue = {
    datasetKey,
    data,
    steps,
    index,
    setIndex,
    raw,
    step,
    changed,
    decisions,
    decide,
    resetReview,
    isReviewStep: raw.id === "day-10",
    pendingReview,
  };

  return <DemoContext.Provider value={value}>{children}</DemoContext.Provider>;
}

export function useDemo(): DemoContextValue {
  const ctx = useContext(DemoContext);
  if (!ctx) throw new Error("useDemo must be used inside DemoProvider");
  return ctx;
}
