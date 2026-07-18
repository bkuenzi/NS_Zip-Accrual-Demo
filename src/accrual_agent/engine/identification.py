"""Uninvoiced-spend identification: NetSuite + Zip -> accrual register.

Three estimate bases, in order of evidence strength:
  1. receipts-not-billed — goods/services received in the period with no bill
  2. PO-fallback — service POs with no receipts: open balance prorated to the
     period's share of the service window
  3. Zip committed spend — approved non-PO engagements with no NetSuite bill

Below-materiality gaps are logged, never accrued or emailed. Lines whose
vendor has no GL mapping (and none on the source document) are still created
so they're visible, but can't post until a human maps them.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import GLMappingStore, Settings
from ..fiscal import Period
from ..integrations.factory import AdapterSet
from ..logging_setup import get_logger
from ..models import PurchaseOrder, SourceType, Vendor, VendorBill, ZipRequisition
from ..register.service import RegisterService

log = get_logger(__name__)

TWO_PLACES = Decimal("0.01")


class IdentificationService:
    def __init__(
        self,
        settings: Settings,
        adapters: AdapterSet,
        register: RegisterService,
        gl_store: GLMappingStore,
    ) -> None:
        self.settings = settings
        self.adapters = adapters
        self.register = register
        self.gl_store = gl_store

    def run(self, period: Period) -> tuple[int, int]:
        """Identify uninvoiced gaps for `period`; returns (created, updated)."""
        ns = self.adapters.netsuite
        vendors = {v.vendor_id: v for v in ns.get_vendors()}
        pos = {po.po_number: po for po in ns.get_open_purchase_orders()}
        receipts = ns.get_goods_receipts(period.start, period.end)
        bills = ns.get_vendor_bills(period.start.replace(day=1), period.end)
        requisitions = self.adapters.zip.get_approved_requisitions(
            period.start, period.end
        )
        # Ad-platform vendors are owned by the API-accrual path; identifying
        # them here as well would double-accrue the same spend.
        api_vendor_ids = {
            m["vendor_id"] for m in self.gl_store.ad_accounts.values()
        }

        created = updated = 0

        # 1. receipts-not-billed, aggregated per PO
        receipts_by_po: dict[str, Decimal] = {}
        for receipt in receipts:
            receipts_by_po[receipt.po_number] = (
                receipts_by_po.get(receipt.po_number, Decimal("0")) + receipt.amount
            )
        for po_number, received in receipts_by_po.items():
            po = pos.get(po_number)
            if po is None or po.vendor_id in api_vendor_ids:
                continue
            billed = sum(
                (b.amount for b in bills if b.po_number == po_number), Decimal("0")
            )
            gap = (received - billed).quantize(TWO_PLACES)
            if gap <= 0:
                continue
            c, u = self._upsert(
                period, vendors.get(po.vendor_id), po, SourceType.NETSUITE_RECEIPT,
                po_number, gap,
                f"goods receipts {received:,.2f} less bills {billed:,.2f} on {po_number}",
            )
            created, updated = created + c, updated + u

        # 2. PO-fallback proration for service POs with no receipts this period
        for po_number, po in pos.items():
            if po_number in receipts_by_po or po.vendor_id in api_vendor_ids:
                continue
            for line in po.lines:
                if line.received_amount > 0 or not (line.service_start and line.service_end):
                    continue
                overlap_start = max(line.service_start, period.start)
                overlap_end = min(line.service_end, period.end)
                if overlap_start > overlap_end:
                    continue
                billed_in_period = sum(
                    (b.amount for b in bills
                     if b.po_number == po_number and b.bill_date >= period.start),
                    Decimal("0"),
                )
                service_days = (line.service_end - line.service_start).days + 1
                period_days = (overlap_end - overlap_start).days + 1
                share = (
                    line.amount * Decimal(period_days) / Decimal(service_days)
                ).quantize(TWO_PLACES)
                gap = (share - billed_in_period).quantize(TWO_PLACES)
                if gap <= 0:
                    continue
                c, u = self._upsert(
                    period, vendors.get(po.vendor_id), po, SourceType.NETSUITE_PO,
                    po_number, gap,
                    f"prorated {period_days}/{service_days} days of {line.amount:,.2f} "
                    f"service PO {po_number}, less {billed_in_period:,.2f} billed in period",
                )
                created, updated = created + c, updated + u

        # 3. Zip non-PO committed spend with no NetSuite bill
        for req in requisitions:
            if req.po_number or req.vendor_id in api_vendor_ids:
                continue  # PO'd requisitions are owned by the NetSuite paths
            vendor_bills = [
                b for b in bills
                if b.vendor_id == req.vendor_id and b.po_number is None
                and (b.service_period == period.name or b.bill_date >= period.start)
            ]
            billed = sum((b.amount for b in vendor_bills), Decimal("0"))
            gap = (req.committed_amount - billed).quantize(TWO_PLACES)
            if gap <= 0:
                continue
            c, u = self._upsert_zip(period, req, vendors.get(req.vendor_id), gap, bills)
            created, updated = created + c, updated + u

        log.info("identification.done", period=period.name, created=created, updated=updated)
        return created, updated

    # ── helpers ──────────────────────────────────────────────────────────

    def _fx(self, currency: str, period: Period) -> Decimal:
        return self.adapters.netsuite.get_exchange_rate(currency, period.end)

    def _below_floor(self, amount: Decimal, currency: str, period: Period, ref: str) -> bool:
        base = amount * self._fx(currency, period)
        if base < self.settings.materiality_floor:
            log.info(
                "identification.below_materiality",
                ref=ref, amount=str(amount), currency=currency,
                floor=str(self.settings.materiality_floor),
            )
            self.register.repo.add_audit(
                None, "accrual-agent", "identification", "below_materiality",
                None, f"{ref}: {amount} {currency}",
            )
            return True
        return False

    def _upsert(
        self,
        period: Period,
        vendor: Vendor | None,
        po: PurchaseOrder,
        source_type: SourceType,
        source_ref: str,
        amount: Decimal,
        basis: str,
    ) -> tuple[int, int]:
        if self._below_floor(amount, po.currency, period, source_ref):
            return 0, 0
        po_line = po.lines[0] if po.lines else None
        mapping = self.gl_store.coding_for(po.vendor_id)
        gl_account = (po_line.gl_account if po_line else None) or (
            mapping.gl_account if mapping else None
        )
        cost_center = (po_line.cost_center if po_line else None) or (
            mapping.cost_center if mapping else None
        )
        _, created = self.register.upsert_line(
            vendor_id=po.vendor_id,
            vendor_name=vendor.name if vendor else po.vendor_id,
            period=period.name,
            source_type=source_type,
            source_ref=source_ref,
            estimate_basis=basis,
            amount=amount,
            currency=po.currency,
            exchange_rate=self._fx(po.currency, period),
            gl_account=gl_account,
            cost_center=cost_center,
            subsidiary_id=po.subsidiary_id,     # source-document subsidiary
        )
        return (1, 0) if created else (0, 1)

    def _upsert_zip(
        self,
        period: Period,
        req: ZipRequisition,
        vendor: Vendor | None,
        amount: Decimal,
        bills: list[VendorBill],
    ) -> tuple[int, int]:
        if self._below_floor(amount, req.currency, period, req.requisition_id):
            return 0, 0
        mapping = self.gl_store.coding_for(req.vendor_id)
        subsidiary = self.gl_store.zip_business_units.get(req.business_unit)
        _, created = self.register.upsert_line(
            vendor_id=req.vendor_id,
            vendor_name=req.vendor_name or (vendor.name if vendor else req.vendor_id),
            period=period.name,
            source_type=SourceType.ZIP_REQUISITION,
            source_ref=req.requisition_id,
            estimate_basis=(
                f"Zip committed spend {req.committed_amount:,.2f} on "
                f"{req.requisition_id}, no matching AP bill"
            ),
            amount=amount,
            currency=req.currency,
            exchange_rate=self._fx(req.currency, period),
            gl_account=req.gl_account or (mapping.gl_account if mapping else None),
            cost_center=req.cost_center or (mapping.cost_center if mapping else None),
            subsidiary_id=subsidiary,
        )
        return (1, 0) if created else (0, 1)
