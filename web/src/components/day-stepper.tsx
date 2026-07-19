"use client";

import { Check, ChevronLeft, ChevronRight, RotateCcw } from "lucide-react";
import { useEffect } from "react";
import { useDemo } from "./demo-context";
import { cn } from "@/lib/cn";
import { fmtDate } from "@/lib/format";

export function DayStepper() {
  const {
    steps,
    index,
    setIndex,
    raw,
    decisions,
    resetReview,
    pendingReview,
    isReviewStep,
  } = useDemo();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "ArrowRight") setIndex(index + 1);
      if (e.key === "ArrowLeft") setIndex(index - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, setIndex]);

  const reviewDone = isReviewStep && pendingReview.length === 0 && Object.keys(decisions).length > 0;

  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <button
          aria-label="Previous day"
          onClick={() => setIndex(index - 1)}
          disabled={index === 0}
          className="cursor-pointer rounded-md p-1 text-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronLeft className="size-4" />
        </button>

        <ol className="flex flex-1 flex-wrap items-center gap-1">
          {steps.map((s, i) => {
            const active = i === index;
            const past = i < index;
            return (
              <li key={s.id} className="flex items-center">
                {i > 0 && (
                  <span
                    className={cn(
                      "mx-0.5 hidden h-px w-4 sm:block",
                      past || active ? "bg-accent" : "bg-border"
                    )}
                  />
                )}
                <button
                  onClick={() => setIndex(i)}
                  className={cn(
                    "cursor-pointer rounded-full px-3 py-1 text-xs font-medium transition-colors",
                    active
                      ? "bg-accent text-white dark:text-slate-900"
                      : past
                        ? "text-accent hover:bg-accent/10"
                        : "text-muted hover:bg-border/60 hover:text-foreground"
                  )}
                >
                  {s.label}
                </button>
              </li>
            );
          })}
        </ol>

        <button
          aria-label="Next day"
          onClick={() => setIndex(index + 1)}
          disabled={index === steps.length - 1}
          className="cursor-pointer rounded-md p-1 text-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronRight className="size-4" />
        </button>

        {Object.keys(decisions).length > 0 && (
          <button
            onClick={resetReview}
            className="inline-flex cursor-pointer items-center gap-1 rounded-md px-2 py-1 text-[11px] text-muted hover:text-foreground"
            title="Clear your approve/reject decisions"
          >
            <RotateCcw className="size-3" /> reset review
          </button>
        )}
      </div>

      <p className="mt-2 border-t border-border pt-2 text-xs leading-relaxed text-muted">
        <span className="mr-2 font-semibold text-foreground">
          {raw.label} · {fmtDate(raw.date)}
        </span>
        {raw.narration}
      </p>

      {reviewDone && (
        <div className="mt-2 flex items-center justify-between gap-3 rounded-lg bg-green-50 px-3 py-2 text-xs text-green-800 dark:bg-green-500/10 dark:text-green-300">
          <span className="inline-flex items-center gap-1.5">
            <Check className="size-3.5" /> Review complete — every held accrual has a
            decision.
          </span>
          <button
            onClick={() => setIndex(steps.length - 1)}
            className="cursor-pointer font-semibold underline-offset-2 hover:underline"
          >
            Advance to Final →
          </button>
        </div>
      )}
    </div>
  );
}
