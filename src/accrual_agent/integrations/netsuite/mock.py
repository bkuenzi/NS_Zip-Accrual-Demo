"""Seeded NetSuite mock for credential-free end-to-end runs.

The dataset deliberately exercises every engine path for period 2026-06:

  V-ACME    receipts-not-billed $28,500; vendor confirms by email; July bill clears it
  V-BETA    receipts-not-billed $18,750; never replies -> full reminder ladder + escalation
  V-ZETA    EUR receipts-not-billed EUR 12,000 (subsidiary 2); confirms via messy reply (LLM path)
  V-ETA     service PO with no receipts -> prorated PO-fallback estimate
  V-DELTA   receipts-not-billed but NO GL mapping -> unmapped-vendor escalation
  V-EPSILON receipts-not-billed but no verified contact -> blocked send
  V-THETA   receipts-not-billed $30,000; vendor reports $33,500 invoice -> variance hold
  V-GOOGLE  partially billed; remainder covered by Google Ads API actuals
  V-META    unbilled; covered by Meta API actuals
"""

from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal

from ...logging_setup import get_logger
from ...models import (
    GoodsReceipt,
    JournalEntry,
    PurchaseOrder,
    PurchaseOrderLine,
    Subsidiary,
    Vendor,
    VendorBill,
)

log = get_logger(__name__)

D = Decimal


def _vendor(vid: str, name: str, domain: str, sub: str = "1", currency: str = "USD") -> Vendor:
    return Vendor(
        vendor_id=vid, name=name, subsidiary_id=sub,
        email_domains=[domain], currency=currency,
    )


class MockNetSuite:
    """In-memory NetSuite satisfying the NetSuiteAdapter protocol."""

    def __init__(self) -> None:
        self.vendors = [
            _vendor("V-ACME", "Acme Cloud Services", "acmecloud.example"),
            _vendor("V-BETA", "Beta Logistics", "betalogistics.example"),
            _vendor("V-GAMMA", "Gamma Consulting", "gammaconsulting.example"),
            _vendor("V-GOOGLE", "Google LLC", "google.com"),
            _vendor("V-META", "Meta Platforms Inc", "meta.com"),
            _vendor("V-DELTA", "Delta Staffing Partners", "deltastaffing.example"),
            _vendor("V-EPSILON", "Epsilon Facilities Group", "epsilonfacilities.example"),
            _vendor("V-ZETA", "Zeta GmbH", "zetagmbh.example", sub="2", currency="EUR"),
            _vendor("V-ETA", "Eta Media Production", "etamedia.example"),
            _vendor("V-THETA", "Theta Software Ltd", "thetasoftware.example"),
        ]
        self.subsidiaries = [
            Subsidiary(subsidiary_id="1", name="YourCo US Inc", currency="USD"),
            Subsidiary(subsidiary_id="2", name="YourCo GmbH", currency="EUR"),
        ]
        self.purchase_orders = [
            PurchaseOrder(
                po_number="PO-1001", vendor_id="V-ACME", subsidiary_id="1",
                lines=[PurchaseOrderLine(
                    line_id="1", description="Cloud hosting June 2026",
                    gl_account="6210", cost_center="CC-ENG",
                    amount=D("42000.00"), received_amount=D("28500.00"),
                )],
            ),
            PurchaseOrder(
                po_number="PO-1002", vendor_id="V-BETA", subsidiary_id="1",
                lines=[PurchaseOrderLine(
                    line_id="1", description="Freight June 2026",
                    gl_account="6410", cost_center="CC-OPS",
                    amount=D("18750.00"), received_amount=D("18750.00"),
                )],
            ),
            PurchaseOrder(
                po_number="PO-1003", vendor_id="V-ZETA", subsidiary_id="2",
                currency="EUR",
                lines=[PurchaseOrderLine(
                    line_id="1", description="EU data-platform consulting June",
                    gl_account="6620", cost_center="CC-ENG",
                    amount=D("24000.00"), received_amount=D("12000.00"),
                )],
            ),
            PurchaseOrder(
                po_number="PO-1004", vendor_id="V-ETA", subsidiary_id="1",
                lines=[PurchaseOrderLine(
                    line_id="1", description="Brand campaign production May-Jul",
                    gl_account="6520", cost_center="CC-MKT",
                    amount=D("60000.00"), billed_amount=D("19565.22"),
                    service_start=dt.date(2026, 5, 1), service_end=dt.date(2026, 7, 31),
                )],
            ),
            PurchaseOrder(
                po_number="PO-1005", vendor_id="V-DELTA", subsidiary_id="1",
                lines=[PurchaseOrderLine(
                    line_id="1", description="Contract staffing June",
                    gl_account=None, cost_center=None,        # unmapped-vendor path
                    amount=D("9800.00"), received_amount=D("9800.00"),
                )],
            ),
            PurchaseOrder(
                po_number="PO-1006", vendor_id="V-EPSILON", subsidiary_id="1",
                lines=[PurchaseOrderLine(
                    line_id="1", description="HVAC maintenance June",
                    gl_account="6710", cost_center="CC-OPS",
                    amount=D("5200.00"), received_amount=D("5200.00"),
                )],
            ),
            PurchaseOrder(
                po_number="PO-1007", vendor_id="V-THETA", subsidiary_id="1",
                lines=[PurchaseOrderLine(
                    line_id="1", description="Analytics platform licenses June",
                    gl_account="6220", cost_center="CC-IT",
                    amount=D("30000.00"), received_amount=D("30000.00"),
                )],
            ),
        ]
        self.receipts = [
            GoodsReceipt(receipt_id="IR-3001", po_number="PO-1001", po_line_id="1",
                         vendor_id="V-ACME", received_date=dt.date(2026, 6, 28),
                         amount=D("28500.00")),
            GoodsReceipt(receipt_id="IR-3002", po_number="PO-1002", po_line_id="1",
                         vendor_id="V-BETA", received_date=dt.date(2026, 6, 25),
                         amount=D("18750.00")),
            GoodsReceipt(receipt_id="IR-3003", po_number="PO-1003", po_line_id="1",
                         vendor_id="V-ZETA", received_date=dt.date(2026, 6, 30),
                         amount=D("12000.00"), currency="EUR"),
            GoodsReceipt(receipt_id="IR-3004", po_number="PO-1005", po_line_id="1",
                         vendor_id="V-DELTA", received_date=dt.date(2026, 6, 22),
                         amount=D("9800.00")),
            GoodsReceipt(receipt_id="IR-3005", po_number="PO-1006", po_line_id="1",
                         vendor_id="V-EPSILON", received_date=dt.date(2026, 6, 18),
                         amount=D("5200.00")),
            GoodsReceipt(receipt_id="IR-3006", po_number="PO-1007", po_line_id="1",
                         vendor_id="V-THETA", received_date=dt.date(2026, 6, 30),
                         amount=D("30000.00")),
        ]
        self.bills = [
            # June bills already in AP (reduce the Google API accrual to a net gap)
            VendorBill(bill_id="B-7001", vendor_id="V-GOOGLE",
                       invoice_number="GOOG-JUN-A", amount=D("100000.00"),
                       bill_date=dt.date(2026, 6, 20), service_period="2026-06"),
            # July arrivals used for next-period reversal clearing
            VendorBill(bill_id="B-7101", vendor_id="V-ACME", invoice_number="INV-8801",
                       po_number="PO-1001", amount=D("28500.00"),
                       bill_date=dt.date(2026, 7, 8), service_period="2026-06"),
            VendorBill(bill_id="B-7102", vendor_id="V-ZETA", invoice_number="RE-2211",
                       po_number="PO-1003", amount=D("12000.00"), currency="EUR",
                       bill_date=dt.date(2026, 7, 10), service_period="2026-06"),
            VendorBill(bill_id="B-7103", vendor_id="V-THETA", invoice_number="INV-5150",
                       po_number="PO-1007", amount=D("33500.00"),
                       bill_date=dt.date(2026, 7, 9), service_period="2026-06"),
        ]
        # NetSuite currency-rate table (period-end effective rows), to base USD
        self.exchange_rates = {"USD": D("1"), "EUR": D("1.09")}
        self.posted_journal_entries: dict[str, JournalEntry] = {}   # by external_id

    # ── NetSuiteAdapter protocol ─────────────────────────────────────────

    def get_vendors(self) -> list[Vendor]:
        return list(self.vendors)

    def get_subsidiaries(self) -> list[Subsidiary]:
        return list(self.subsidiaries)

    def get_open_purchase_orders(self) -> list[PurchaseOrder]:
        return list(self.purchase_orders)

    def get_goods_receipts(self, start: dt.date, end: dt.date) -> list[GoodsReceipt]:
        return [r for r in self.receipts if start <= r.received_date <= end]

    def get_vendor_bills(self, start: dt.date, end: dt.date) -> list[VendorBill]:
        return [b for b in self.bills if start <= b.bill_date <= end]

    def get_exchange_rate(self, currency: str, as_of: dt.date) -> Decimal:
        return self.exchange_rates[currency]

    def post_journal_entry(self, je: JournalEntry) -> str:
        existing = self.posted_journal_entries.get(je.external_id)
        if existing is not None:
            log.info("mock_netsuite.je_dedupe", external_id=je.external_id,
                     netsuite_id=existing.netsuite_id)
            return existing.netsuite_id or ""
        # Deterministic per external_id so ids stay stable across process
        # restarts (the mock is rebuilt every CLI invocation).
        digest = int(hashlib.sha256(je.external_id.encode()).hexdigest()[:8], 16)
        internal_id = f"JE-{10000 + digest % 90000}"
        self.posted_journal_entries[je.external_id] = je.model_copy(
            update={"netsuite_id": internal_id}
        )
        log.info("mock_netsuite.je_posted", external_id=je.external_id,
                 netsuite_id=internal_id, amount=str(je.amount), currency=je.currency)
        return internal_id
