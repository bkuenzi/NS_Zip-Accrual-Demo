"use client";

import { CheckCircle2 } from "lucide-react";
import { AttentionList } from "@/components/attention-list";
import { AuditTable } from "@/components/audit-table";
import { ReviewQueue } from "@/components/review-queue";
import { useDemo } from "@/components/demo-context";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader } from "@/components/ui/card";
import { SEVERITY_BADGE } from "@/lib/status";
import { money } from "@/lib/format";

function Escalations() {
  const { step } = useDemo();
  if (step.escalations.length === 0) return null;
  return (
    <Card>
      <CardHeader
        title={`Escalations (${step.escalations.length})`}
        subtitle="What the agent raised to humans, and what it suggests doing"
      />
      <ul className="px-5 pb-4">
        {step.escalations.map((e) => (
          <li
            key={e.escalation_id}
            className="border-b border-border py-3 text-xs leading-relaxed last:border-b-0"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Badge className={SEVERITY_BADGE[e.severity]}>{e.severity}</Badge>
              <b>{e.label}</b>
              {e.line_id ? <span className="text-muted">{e.line_id}</span> : null}
              {e.raise_count > 1 ? (
                <span className="text-muted">raised ×{e.raise_count}</span>
              ) : null}
            </div>
            <p className="mt-1 text-muted">{e.detail}</p>
            {e.suggested_action ? (
              <p className="mt-1 text-muted">
                <span className="font-medium text-foreground">suggested:</span>{" "}
                {e.suggested_action}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Approvals() {
  const { step } = useDemo();
  if (step.approvals.length === 0) return null;
  return (
    <Card className="border-green-200 dark:border-green-500/30">
      <CardHeader
        title="Controller approvals"
        subtitle="Held variances reviewed and posted during the close"
      />
      <ul className="px-5 pb-4">
        {step.approvals.map((a) => (
          <li
            key={a.line_id}
            className="flex items-start gap-2.5 border-b border-border py-2 text-xs last:border-b-0"
          >
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-green-600 dark:text-green-400" />
            <span>
              <b>
                {a.line_id} {a.vendor_name}
              </b>{" "}
              — {money(a.amount, a.currency)} approved by {a.actor} → JE {a.je_id}
              <span className="text-muted"> · {a.note}</span>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function AgentActivity() {
  const { step } = useDemo();
  const r = step.runResult;
  const stats: Array<[string, number]> = [
    ["lines created", r.lines_created],
    ["lines updated", r.lines_updated],
    ["emails sent", r.emails_sent],
    ["replies processed", r.replies_processed],
    ["JEs posted", r.jes_posted],
    ["lines cleared", r.lines_cleared],
    ["escalations raised", r.escalations_raised],
  ];
  return (
    <Card>
      <CardHeader
        title={`Agent activity — ${step.label}`}
        subtitle={`Stages run: ${r.stages_run.join(" → ")}`}
      />
      <div className="flex flex-wrap gap-x-6 gap-y-2 px-5 pb-4">
        {stats.map(([label, value]) => (
          <div key={label} className="text-xs text-muted">
            <b className="mr-1 text-sm text-foreground tabular-nums">{value}</b>
            {label}
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function AttentionPage() {
  const { step } = useDemo();
  return (
    <div className="flex flex-col gap-4">
      <ReviewQueue />
      <Approvals />
      <AttentionList />
      <Escalations />
      <AgentActivity />
      <Card>
        <CardHeader
          title={`Audit trail (${step.audit.length})`}
          subtitle="Immutable log of every register change the agent made"
        />
        <div className="px-5 pb-4">
          <AuditTable rows={step.audit} />
        </div>
      </Card>
    </div>
  );
}
