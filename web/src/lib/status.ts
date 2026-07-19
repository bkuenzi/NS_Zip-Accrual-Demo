// UI labels + badge styling for the Python StrEnum values.
// Colors ported from STATUS_BADGES in src/accrual_agent/reporting/dashboard.py.

import type { AccrualStatus, SourceType, ThreadStatus } from "./types";

export const STATUS_META: Record<
  AccrualStatus,
  { label: string; badge: string; dot: string }
> = {
  estimated_pending_confirmation: {
    label: "estimated",
    badge:
      "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
    dot: "bg-amber-500",
  },
  confirmed: {
    label: "confirmed",
    badge:
      "bg-green-100 text-green-800 dark:bg-green-500/15 dark:text-green-300",
    dot: "bg-green-500",
  },
  auto_confirmed: {
    label: "auto-confirmed",
    badge: "bg-cyan-100 text-cyan-800 dark:bg-cyan-500/15 dark:text-cyan-300",
    dot: "bg-cyan-500",
  },
  held_for_review: {
    label: "held for review",
    badge: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-300",
    dot: "bg-red-500",
  },
  posted: {
    label: "posted",
    badge: "bg-blue-100 text-blue-800 dark:bg-blue-500/15 dark:text-blue-300",
    dot: "bg-blue-500",
  },
  cleared: {
    label: "cleared",
    badge:
      "bg-slate-200 text-slate-700 dark:bg-slate-500/20 dark:text-slate-300",
    dot: "bg-slate-500",
  },
  rejected: {
    label: "rejected",
    badge:
      "bg-slate-100 text-slate-500 dark:bg-slate-500/10 dark:text-slate-400",
    dot: "bg-slate-400",
  },
};

// Chart colors per status. Stack order + hex steps validated with the
// dataviz palette checker (CVD separation, normal-vision floor, contrast)
// against the light (#ffffff) and dark (#111a2e) card surfaces. Segments
// are always direct-labeled, so color never carries meaning alone.
export const CHART_STATUS_ORDER: AccrualStatus[] = [
  "estimated_pending_confirmation",
  "posted",
  "held_for_review",
  "auto_confirmed",
  "confirmed",
  "cleared",
];

export const STATUS_COLORS: Record<AccrualStatus, { light: string; dark: string }> = {
  estimated_pending_confirmation: { light: "#b45309", dark: "#d97706" },
  posted: { light: "#1d4ed8", dark: "#2563eb" },
  held_for_review: { light: "#b91c1c", dark: "#dc2626" },
  auto_confirmed: { light: "#0369a1", dark: "#0284c7" },
  confirmed: { light: "#15803d", dark: "#16a34a" },
  cleared: { light: "#475569", dark: "#64748b" },
  rejected: { light: "#94a3b8", dark: "#94a3b8" },
};

export const THREAD_LABELS: Record<ThreadStatus, string> = {
  not_started: "not started",
  awaiting_reply: "awaiting reply",
  replied: "replied",
  blocked_no_contact: "blocked — no contact",
  suppressed_api: "API-confirmed (no email)",
  exhausted: "non-responsive",
};

export const SOURCE_LABELS: Record<SourceType, string> = {
  netsuite_receipt: "NetSuite receipt",
  netsuite_po: "NetSuite PO",
  zip_requisition: "Zip requisition",
  google_ads: "Google Ads",
  meta_ads: "Meta Ads",
};

export const SEVERITY_BADGE: Record<string, string> = {
  low: "bg-slate-100 text-slate-600 dark:bg-slate-500/15 dark:text-slate-300",
  medium: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
  high: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-300",
};
