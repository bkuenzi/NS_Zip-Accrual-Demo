// TypeScript mirror of the JSON snapshot schema produced by
// src/accrual_agent/reporting/web_export.py. Enum unions are copied from
// the StrEnum values in src/accrual_agent/models.py.

export type AccrualStatus =
  | "estimated_pending_confirmation"
  | "confirmed"
  | "auto_confirmed"
  | "held_for_review"
  | "posted"
  | "cleared"
  | "rejected";

export type SourceType =
  | "netsuite_receipt"
  | "netsuite_po"
  | "zip_requisition"
  | "google_ads"
  | "meta_ads";

export type ThreadStatus =
  | "not_started"
  | "awaiting_reply"
  | "replied"
  | "blocked_no_contact"
  | "suppressed_api"
  | "exhausted";

export interface AccrualLine {
  line_id: string;
  vendor_id: string;
  vendor_name: string;
  period: string;
  source_type: SourceType;
  source_ref: string;
  estimate_basis: string;
  amount: string;
  currency: string;
  exchange_rate: string;
  base_amount: string;
  gl_account: string | null;
  cost_center: string | null;
  subsidiary_id: string | null;
  status: AccrualStatus;
  provisional: boolean;
  comm_suppressed: boolean;
  ref_token: string;
  thread_status: ThreadStatus;
  close_risk: boolean;
  confirmed_amount: string | null;
  confirmed_source: string | null;
  invoice_number: string | null;
  invoice_eta: string | null;
  hold_reason: string | null;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
  postable_amount: string;
}

export interface JournalEntry {
  je_id: number;
  line_id: string;
  external_id: string;
  tran_date: string;
  reversal_date: string;
  subsidiary_id: string;
  debit_account: string;
  credit_account: string;
  amount: string;
  currency: string;
  exchange_rate: string;
  memo: string;
  estimate_based: boolean;
  netsuite_id: string | null;
  posted_at: string | null;
}

export interface Escalation {
  escalation_id: number;
  line_id: string | null;
  reason: string;
  severity: "low" | "medium" | "high";
  detail: string;
  suggested_action: string;
  raised_at: string | null;
  last_raised_at: string | null;
  raise_count: number;
  resolved_at: string | null;
  channels: string[];
  label: string;
}

export interface CommRecord {
  comm_id: number | null;
  line_id: string;
  direction: "outbound" | "inbound";
  stage: string;
  recipient: string | null;
  sender: string | null;
  subject: string;
  message_id: string | null;
  in_reply_to: string | null;
  body_preview: string;
  attachment_paths: string[];
  sent_at: string | null;
  delivery: string;
}

export interface AuditRow {
  ts: string;
  line_id: string | null;
  actor: string;
  source: string;
  field: string;
  old_value: string | null;
  new_value: string | null;
}

export interface RunError {
  stage: string;
  error: string;
  line_id: string | null;
}

export interface RunResult {
  period: string;
  close_day: number;
  started_at: string;
  finished_at: string | null;
  stages_run: string[];
  lines_created: number;
  lines_updated: number;
  emails_sent: number;
  replies_processed: number;
  jes_posted: number;
  lines_cleared: number;
  escalations_raised: number;
  errors: RunError[];
}

export interface Kpis {
  lineCount: number;
  baseTotal: string;
  postedTotal: string;
  unconfirmed: number;
  held: number;
  openEscalations: number;
  posted: number;
}

export interface Approval {
  line_id: string;
  je_id: string;
  vendor_name: string;
  amount: string;
  currency: string;
  actor: string;
  note: string;
}

export interface DemoStep {
  id: string;
  closeDay: number;
  label: string;
  date: string;
  narration: string;
  runResult: RunResult;
  kpis: Kpis;
  lines: AccrualLine[];
  journalEntries: JournalEntry[];
  escalations: Escalation[];
  comms: Record<string, CommRecord[]>;
  audit: AuditRow[];
  reviewQueue: string[];
  approvals: Approval[];
}

export interface DemoData {
  generatedAt: string;
  period: string;
  baseCurrency: string;
  finalCloseDay: number;
  steps: DemoStep[];
}
