"""Adapter interfaces the engine depends on.

Real clients and mocks both satisfy these Protocols; the factory picks per
settings.mode. Note the Zip interface is read-only by construction — there is
deliberately no write method to implement.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Protocol

from ..models import (
    AdSpendRecord,
    GoodsReceipt,
    JournalEntry,
    PurchaseOrder,
    Subsidiary,
    Vendor,
    VendorBill,
    ZipRequisition,
)


class NetSuiteAdapter(Protocol):
    def get_vendors(self) -> list[Vendor]: ...

    def get_subsidiaries(self) -> list[Subsidiary]: ...

    def get_open_purchase_orders(self) -> list[PurchaseOrder]: ...

    def get_goods_receipts(self, start: dt.date, end: dt.date) -> list[GoodsReceipt]: ...

    def get_vendor_bills(self, start: dt.date, end: dt.date) -> list[VendorBill]: ...

    def get_exchange_rate(self, currency: str, as_of: dt.date) -> Decimal: ...

    def post_journal_entry(self, je: JournalEntry) -> str:
        """Post a JE; returns the NetSuite internal id. Must honor external_id
        dedupe: re-posting the same external_id returns the existing id."""
        ...


class ZipAdapter(Protocol):
    """Read-only: approved requisitions / committed spend / engagements."""

    def get_approved_requisitions(self, start: dt.date, end: dt.date) -> list[ZipRequisition]: ...


class AdPlatformAdapter(Protocol):
    platform_name: str

    def get_spend(self, start: dt.date, end: dt.date) -> list[AdSpendRecord]: ...
