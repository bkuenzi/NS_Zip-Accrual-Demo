"""Loader for the standalone SeatGeek accounting dataset.

Reads ``datasets/seatgeek/seatgeek_dataset.json`` (produced by
``scripts/generate_seatgeek_dataset.py``) once and materializes it into the
shared domain models the accrual engine consumes. The dataset is deliberately
kept separate from the toy demo fixtures — this loader is the only bridge
between the two, used exclusively by the ``mvp`` profile's adapters.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from ...config import PROJECT_ROOT
from ...models import (
    GoodsReceipt,
    PurchaseOrder,
    PurchaseOrderLine,
    Subsidiary,
    Vendor,
    VendorBill,
    ZipRequisition,
)

DATASET_PATH = PROJECT_ROOT / "datasets" / "seatgeek" / "seatgeek_dataset.json"


def _opt(value: str | None) -> str | None:
    return value or None


def _date(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None


@dataclass
class SeatGeekData:
    vendors: list[Vendor]
    subsidiaries: list[Subsidiary]
    purchase_orders: list[PurchaseOrder]
    goods_receipts: list[GoodsReceipt]
    vendor_bills: list[VendorBill]
    zip_requisitions: list[ZipRequisition]
    exchange_rates: dict[str, Decimal]
    ad_spend: list[dict] = field(default_factory=list)


@lru_cache(maxsize=1)
def load_seatgeek_data(path: Path = DATASET_PATH) -> SeatGeekData:
    if not path.exists():
        raise FileNotFoundError(
            f"SeatGeek dataset not found at {path}. Run "
            "`python scripts/generate_seatgeek_dataset.py` first."
        )
    raw = json.loads(path.read_text())

    vendors = [
        Vendor(
            vendor_id=v["vendor_id"],
            name=v["name"],
            subsidiary_id=v.get("subsidiary_id"),
            email_domains=[v["domain"]] if v.get("domain") else [],
            currency=v.get("currency", "USD"),
        )
        for v in raw["vendors"]
    ]

    subsidiaries = [
        Subsidiary(
            subsidiary_id=s["subsidiary_id"], name=s["name"],
            currency=s.get("currency", "USD"),
        )
        for s in raw["subsidiaries"]
    ]

    lines_by_po: dict[str, list[PurchaseOrderLine]] = {}
    for ln in raw["purchase_order_lines"]:
        lines_by_po.setdefault(ln["po_number"], []).append(
            PurchaseOrderLine(
                line_id=ln["line_id"],
                description=ln["description"],
                gl_account=_opt(ln.get("gl_account")),
                cost_center=_opt(ln.get("department")),
                amount=Decimal(ln["amount"]),
                billed_amount=Decimal(ln.get("billed_amount") or "0"),
                received_amount=Decimal(ln.get("received_amount") or "0"),
                service_start=_date(ln.get("service_start")),
                service_end=_date(ln.get("service_end")),
            )
        )
    purchase_orders = [
        PurchaseOrder(
            po_number=po["po_number"], vendor_id=po["vendor_id"],
            subsidiary_id=po["subsidiary_id"], currency=po.get("currency", "USD"),
            status=po.get("status", "open"),
            lines=lines_by_po.get(po["po_number"], []),
        )
        for po in raw["purchase_orders"]
    ]

    goods_receipts = [
        GoodsReceipt(
            receipt_id=r["receipt_id"], po_number=r["po_number"],
            po_line_id=r["po_line_id"], vendor_id=r["vendor_id"],
            received_date=dt.date.fromisoformat(r["received_date"]),
            amount=Decimal(r["amount"]), currency=r.get("currency", "USD"),
        )
        for r in raw["goods_receipts"]
    ]

    vendor_bills = [
        VendorBill(
            bill_id=b["bill_id"], vendor_id=b["vendor_id"],
            invoice_number=b["invoice_number"], po_number=_opt(b.get("po_number")),
            amount=Decimal(b["amount"]), currency=b.get("currency", "USD"),
            bill_date=dt.date.fromisoformat(b["bill_date"]),
            service_period=_opt(b.get("service_period")),
        )
        for b in raw["vendor_bills"]
    ]

    zip_requisitions = [
        ZipRequisition(
            requisition_id=z["requisition_id"], vendor_id=z["vendor_id"],
            vendor_name=z["vendor_name"], business_unit=z["business_unit"],
            committed_amount=Decimal(z["committed_amount"]),
            currency=z.get("currency", "USD"),
            approved_date=dt.date.fromisoformat(z["approved_date"]),
            service_start=_date(z.get("service_start")),
            service_end=_date(z.get("service_end")),
            po_number=_opt(z.get("po_number")),
            gl_account=_opt(z.get("gl_account")),
            cost_center=_opt(z.get("department")),
        )
        for z in raw["zip_requisitions"]
    ]

    exchange_rates = {
        r["currency"]: Decimal(r["rate_to_usd"]) for r in raw["exchange_rates"]
    }
    exchange_rates.setdefault("USD", Decimal("1"))

    return SeatGeekData(
        vendors=vendors,
        subsidiaries=subsidiaries,
        purchase_orders=purchase_orders,
        goods_receipts=goods_receipts,
        vendor_bills=vendor_bills,
        zip_requisitions=zip_requisitions,
        exchange_rates=exchange_rates,
        ad_spend=list(raw.get("ad_spend", [])),
    )
