"""Credential-free adapters backed by the SeatGeek dataset (``mvp`` profile).

Same protocols as the demo mocks and the live clients, but every record comes
from ``datasets/seatgeek/`` instead of the inline toy fixtures. Journal-entry
posting is simulated with the same deterministic-external-id dedupe the demo
NetSuite mock uses, so re-runs stay idempotent.
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
    SourceType,
    Subsidiary,
    Vendor,
    VendorBill,
    ZipRequisition,
)
from ..ad_mocks import _MockAdPlatform
from .dataset import load_seatgeek_data

log = get_logger(__name__)


class SeatGeekNetSuite:
    """In-memory NetSuite satisfying the NetSuiteAdapter protocol from data."""

    def __init__(self) -> None:
        self.data = load_seatgeek_data()
        self.posted_journal_entries: dict[str, JournalEntry] = {}

    def get_vendors(self) -> list[Vendor]:
        return list(self.data.vendors)

    def get_subsidiaries(self) -> list[Subsidiary]:
        return list(self.data.subsidiaries)

    def get_open_purchase_orders(self) -> list[PurchaseOrder]:
        return list(self.data.purchase_orders)

    def get_goods_receipts(self, start: dt.date, end: dt.date) -> list[GoodsReceipt]:
        return [r for r in self.data.goods_receipts if start <= r.received_date <= end]

    def get_vendor_bills(self, start: dt.date, end: dt.date) -> list[VendorBill]:
        return [b for b in self.data.vendor_bills if start <= b.bill_date <= end]

    def get_exchange_rate(self, currency: str, as_of: dt.date) -> Decimal:
        return self.data.exchange_rates.get(currency, Decimal("1"))

    def post_journal_entry(self, je: JournalEntry) -> str:
        existing = self.posted_journal_entries.get(je.external_id)
        if existing is not None:
            log.info("seatgeek_netsuite.je_dedupe", external_id=je.external_id,
                     netsuite_id=existing.netsuite_id)
            return existing.netsuite_id or ""
        digest = int(hashlib.sha256(je.external_id.encode()).hexdigest()[:8], 16)
        internal_id = f"JE-{100000 + digest % 900000}"
        self.posted_journal_entries[je.external_id] = je.model_copy(
            update={"netsuite_id": internal_id}
        )
        log.info("seatgeek_netsuite.je_posted", external_id=je.external_id,
                 netsuite_id=internal_id, amount=str(je.amount), currency=je.currency)
        return internal_id


class SeatGeekZip:
    """Read-only Zip adapter over the dataset's approved requisitions."""

    def __init__(self) -> None:
        self.requisitions = load_seatgeek_data().zip_requisitions

    def get_approved_requisitions(
        self, start: dt.date, end: dt.date
    ) -> list[ZipRequisition]:
        return [
            r for r in self.requisitions
            if (r.service_end or r.approved_date) >= start and r.approved_date <= end
        ]


def _ad_platform(platform: SourceType, name: str) -> dict | None:
    for record in load_seatgeek_data().ad_spend:
        if record["platform"] == name:
            return record
    return None


class _SeatGeekAdPlatform(_MockAdPlatform):
    """Dataset-seeded ad platform with the same 72h restatement behavior.

    ``final_spend`` is the settled figure from the dataset; the provisional
    figure returned inside the settle window is a slightly lower restatement.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        record = _ad_platform(self.platform, self.platform_name)
        if record is not None:
            self.account_id = record["account_id"]
            self.final_spend = Decimal(record["spend"])
            self.provisional_spend = (self.final_spend * Decimal("0.994")).quantize(
                Decimal("0.01")
            )


class SeatGeekGoogleAds(_SeatGeekAdPlatform):
    platform = SourceType.GOOGLE_ADS
    platform_name = "google_ads"
    account_id = "GAD-118-4420-9917"
    provisional_spend = Decimal("3220560.00")
    final_spend = Decimal("3240000.00")


class SeatGeekMetaAds(_SeatGeekAdPlatform):
    platform = SourceType.META_ADS
    platform_name = "meta_ads"
    account_id = "act_559002841"
    provisional_spend = Decimal("2639070.00")
    final_spend = Decimal("2655000.00")


def build_seatgeek_adapters(settle_hours: int, now_provider):
    kwargs = {"settle_hours": settle_hours}
    if now_provider is not None:
        kwargs["now_provider"] = now_provider
    return (
        SeatGeekNetSuite(),
        SeatGeekZip(),
        [SeatGeekGoogleAds(**kwargs), SeatGeekMetaAds(**kwargs)],
    )
