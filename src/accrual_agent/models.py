"""Domain models shared across ingestion, comms, engine, and write-back."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AccrualStatus(StrEnum):
    """Lifecycle of an accrual register line.

    Legal transitions are enforced by ``register.service.RegisterService``.
    """

    ESTIMATED = "estimated_pending_confirmation"
    CONFIRMED = "confirmed"
    AUTO_CONFIRMED = "auto_confirmed"
    HELD_FOR_REVIEW = "held_for_review"
    POSTED = "posted"
    CLEARED = "cleared"
    REJECTED = "rejected"


class SourceType(StrEnum):
    NETSUITE_RECEIPT = "netsuite_receipt"      # goods receipt without bill
    NETSUITE_PO = "netsuite_po"                # open service PO, prorated fallback
    ZIP_REQUISITION = "zip_requisition"        # approved non-PO committed spend
    GOOGLE_ADS = "google_ads"                  # ad-platform actuals
    META_ADS = "meta_ads"


API_SOURCE_TYPES = {SourceType.GOOGLE_ADS, SourceType.META_ADS}


class CommStage(StrEnum):
    INITIAL = "initial"
    DAY3 = "day3"
    DAY7 = "day7"
    DAY10 = "day10"


class ThreadStatus(StrEnum):
    NOT_STARTED = "not_started"
    AWAITING_REPLY = "awaiting_reply"
    REPLIED = "replied"
    BLOCKED_NO_CONTACT = "blocked_no_contact"
    SUPPRESSED_API = "suppressed_api"          # API-confirmed line: no vendor email
    EXHAUSTED = "exhausted"                    # final reminder sent, no reply


class EscalationReason(StrEnum):
    VENDOR_NON_RESPONSIVE = "vendor_non_responsive"
    API_FAILURE = "api_failure"
    API_ANOMALY = "api_anomaly"
    VARIANCE_BREACH = "variance_breach"
    UNMAPPED_VENDOR = "unmapped_vendor"
    MISSING_CONTACT = "missing_contact"
    UNRESOLVED_SUBSIDIARY = "unresolved_subsidiary"
    STALE_ACCRUAL = "stale_accrual"
    UNPARSEABLE_REPLY = "unparseable_reply"
    AMBIGUOUS_INVOICE_MATCH = "ambiguous_invoice_match"
    CLOSE_RISK = "close_risk"


ESCALATION_LABELS: dict[EscalationReason, str] = {
    EscalationReason.VENDOR_NON_RESPONSIVE: "Vendor non-responsive after final reminder",
    EscalationReason.API_FAILURE: "Vendor API pull failed",
    EscalationReason.API_ANOMALY: "Vendor API returned unexpected data",
    EscalationReason.VARIANCE_BREACH: "Variance exceeds threshold — held for review",
    EscalationReason.UNMAPPED_VENDOR: "New vendor with no GL mapping",
    EscalationReason.MISSING_CONTACT: "No verified vendor contact on file — send blocked",
    EscalationReason.UNRESOLVED_SUBSIDIARY: "Subsidiary could not be resolved",
    EscalationReason.STALE_ACCRUAL: "Posted accrual unmatched beyond lookback window",
    EscalationReason.UNPARSEABLE_REPLY: "Vendor reply could not be parsed",
    EscalationReason.AMBIGUOUS_INVOICE_MATCH: "Ambiguous invoice-to-accrual match",
    EscalationReason.CLOSE_RISK: "Unconfirmed accrual at close deadline",
}


class Money(BaseModel):
    """Amount in a transaction currency plus its base-currency equivalent."""

    model_config = ConfigDict(frozen=True)

    amount: Decimal
    currency: str = "USD"

    def __str__(self) -> str:  # "1,234.50 USD"
        return f"{self.amount:,.2f} {self.currency}"


# ── Source-system records ────────────────────────────────────────────────────


class Vendor(BaseModel):
    vendor_id: str
    name: str
    subsidiary_id: str | None = None
    email_domains: list[str] = Field(default_factory=list)
    currency: str = "USD"


class PurchaseOrderLine(BaseModel):
    line_id: str
    description: str
    gl_account: str | None = None
    cost_center: str | None = None
    amount: Decimal
    billed_amount: Decimal = Decimal("0")
    received_amount: Decimal = Decimal("0")
    service_start: date | None = None
    service_end: date | None = None


class PurchaseOrder(BaseModel):
    po_number: str
    vendor_id: str
    subsidiary_id: str
    currency: str = "USD"
    status: str = "open"
    lines: list[PurchaseOrderLine] = Field(default_factory=list)


class GoodsReceipt(BaseModel):
    receipt_id: str
    po_number: str
    po_line_id: str
    vendor_id: str
    received_date: date
    amount: Decimal
    currency: str = "USD"


class VendorBill(BaseModel):
    bill_id: str
    vendor_id: str
    invoice_number: str
    po_number: str | None = None
    amount: Decimal
    currency: str = "USD"
    bill_date: date
    service_period: str | None = None       # period name if coded on the bill
    posted: bool = True


class ZipRequisition(BaseModel):
    """Approved Zip requisition / vendor engagement (read-only source)."""

    requisition_id: str
    vendor_id: str
    vendor_name: str
    business_unit: str
    committed_amount: Decimal
    currency: str = "USD"
    approved_date: date
    service_start: date | None = None
    service_end: date | None = None
    po_number: str | None = None            # present when the req became a NetSuite PO
    gl_account: str | None = None
    cost_center: str | None = None


class AdSpendRecord(BaseModel):
    platform: SourceType                     # GOOGLE_ADS or META_ADS
    account_id: str
    period_start: date
    period_end: date
    spend: Decimal
    currency: str = "USD"
    as_of: datetime                          # data freshness timestamp from the pull


class Subsidiary(BaseModel):
    subsidiary_id: str
    name: str
    currency: str = "USD"


# ── Register records ─────────────────────────────────────────────────────────


class AccrualLine(BaseModel):
    """Head row of the accrual register (mutable; changes audit-logged)."""

    line_id: str                             # e.g. ACR-2026-06-0001
    vendor_id: str
    vendor_name: str
    period: str                              # fiscal period name, e.g. 2026-06
    source_type: SourceType
    source_ref: str                          # PO# / requisition id / ad account id
    estimate_basis: str                      # human-readable derivation of the estimate
    amount: Decimal                          # current best accrual amount (txn currency)
    currency: str = "USD"
    exchange_rate: Decimal = Decimal("1")    # period-end spot rate to base currency
    base_amount: Decimal = Decimal("0")      # amount * exchange_rate, base currency
    gl_account: str | None = None
    cost_center: str | None = None
    subsidiary_id: str | None = None
    status: AccrualStatus = AccrualStatus.ESTIMATED
    provisional: bool = False                # ad data still inside the settle window
    comm_suppressed: bool = False            # API-confirmed: no vendor email
    ref_token: str = ""                      # e.g. [ACR-2026-06-0001]
    thread_status: ThreadStatus = ThreadStatus.NOT_STARTED
    close_risk: bool = False
    confirmed_amount: Decimal | None = None  # vendor- or API-confirmed amount
    confirmed_source: str | None = None      # "vendor_reply" / "google_ads" / ...
    invoice_number: str | None = None
    invoice_eta: date | None = None
    hold_reason: str | None = None           # populated when HELD_FOR_REVIEW
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def postable_amount(self) -> Decimal:
        return self.confirmed_amount if self.confirmed_amount is not None else self.amount


class CommRecord(BaseModel):
    comm_id: int | None = None
    line_id: str
    direction: str                           # "outbound" | "inbound"
    stage: str                               # CommStage value, or "reply"
    recipient: str | None = None
    sender: str | None = None
    subject: str
    message_id: str | None = None
    in_reply_to: str | None = None
    body_preview: str = ""
    attachment_paths: list[str] = Field(default_factory=list)
    sent_at: datetime | None = None
    delivery: str = "logged"                 # dry_run | sandbox | live | received


class JournalEntry(BaseModel):
    je_id: int | None = None
    line_id: str
    external_id: str                         # deterministic — NetSuite dedupe key
    tran_date: date
    reversal_date: date                      # NetSuite auto-reversal, day 1 next period
    subsidiary_id: str
    debit_account: str                       # expense GL
    credit_account: str                      # accrued liabilities
    amount: Decimal
    currency: str
    exchange_rate: Decimal
    memo: str
    estimate_based: bool = False
    netsuite_id: str | None = None
    posted_at: datetime | None = None


class Escalation(BaseModel):
    escalation_id: int | None = None
    line_id: str | None = None               # None for run-level issues (e.g. API down)
    reason: EscalationReason
    severity: str = "medium"                 # low | medium | high
    detail: str = ""
    suggested_action: str = ""
    raised_at: datetime | None = None
    last_raised_at: datetime | None = None
    raise_count: int = 1
    resolved_at: datetime | None = None
    channels: list[str] = Field(default_factory=list)


class ParsedVendorReply(BaseModel):
    """Structured extraction from an inbound vendor email."""

    confirmed_amount: Decimal | None = None
    currency: str | None = None
    invoice_number: str | None = None
    expected_invoice_date: date | None = None
    confirms_estimate: bool | None = None    # explicit "confirmed"/"correct" language
    confidence: float = 0.0                  # 0..1
    method: str = "heuristic"                # heuristic | llm | attachment
    raw_excerpt: str = ""


class RunError(BaseModel):
    stage: str
    error: str
    line_id: str | None = None


class RunResult(BaseModel):
    period: str
    close_day: int
    started_at: datetime
    finished_at: datetime | None = None
    stages_run: list[str] = Field(default_factory=list)
    lines_created: int = 0
    lines_updated: int = 0
    emails_sent: int = 0
    replies_processed: int = 0
    jes_posted: int = 0
    lines_cleared: int = 0
    escalations_raised: int = 0
    errors: list[RunError] = Field(default_factory=list)
    checkpoint_report_path: str | None = None
    dashboard_path: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def decimal_str(value: Any) -> str:
    """Canonical string form for storing Decimals in SQLite."""
    return str(Decimal(value))
