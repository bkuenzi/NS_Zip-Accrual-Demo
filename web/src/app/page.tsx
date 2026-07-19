"use client";

import { AttentionList } from "@/components/attention-list";
import { CloseTrend } from "@/components/charts/close-trend";
import { StatusBar } from "@/components/charts/status-bar";
import { KpiCards } from "@/components/kpi-cards";
import { ReviewQueue } from "@/components/review-queue";
import { TrustLadder } from "@/components/trust-ladder";

export default function OverviewPage() {
  return (
    <div className="flex flex-col gap-4">
      <KpiCards />
      <ReviewQueue />
      <div className="grid gap-4 lg:grid-cols-2">
        <StatusBar />
        <CloseTrend />
      </div>
      <TrustLadder />
      <AttentionList limit={6} />
    </div>
  );
}
