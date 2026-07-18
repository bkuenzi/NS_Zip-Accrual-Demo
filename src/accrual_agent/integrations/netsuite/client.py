"""NetSuite SuiteTalk REST client (Token-Based Auth).

Reads: vendors, subsidiaries, open POs, item receipts, vendor bills, currency
rates — via SuiteQL for set-based queries and the record API for JE writes.

Writes: journal entries only. The JE carries a deterministic externalId so a
re-post of the same accrual is rejected/deduped by NetSuite itself, and a
reversalDate so NetSuite auto-reverses the accrual on day 1 of the next
period.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from ...config import Settings
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
from ..base import BaseAPIClient, UpstreamError
from .oauth1 import NetSuiteOAuth1Signer

log = get_logger(__name__)

SUITEQL_PAGE_SIZE = 1000


class NetSuiteClient(BaseAPIClient):
    system = "netsuite"

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        settings.require(
            {
                "NETSUITE_ACCOUNT_ID": settings.netsuite_account_id,
                "NETSUITE_CONSUMER_KEY": settings.netsuite_consumer_key,
                "NETSUITE_CONSUMER_SECRET": settings.netsuite_consumer_secret,
                "NETSUITE_TOKEN_ID": settings.netsuite_token_id,
                "NETSUITE_TOKEN_SECRET": settings.netsuite_token_secret,
            },
            purpose="NetSuite",
        )
        account_slug = settings.netsuite_account_id.lower().replace("_", "-")
        base_url = f"https://{account_slug}.suitetalk.api.netsuite.com/services/rest"
        signer = NetSuiteOAuth1Signer(
            account_id=settings.netsuite_account_id,
            consumer_key=settings.netsuite_consumer_key,
            consumer_secret=settings.netsuite_consumer_secret,
            token_id=settings.netsuite_token_id,
            token_secret=settings.netsuite_token_secret,
        )
        super().__init__(base_url, auth_hook=signer, **kwargs)

    # ── SuiteQL helper with offset pagination ────────────────────────────

    def suiteql(self, query: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self.post_json(
                "/query/v1/suiteql",
                params={"limit": SUITEQL_PAGE_SIZE, "offset": offset},
                json={"q": query},
                headers={"Prefer": "transient"},
            ).json()
            items.extend(payload.get("items", []))
            if not payload.get("hasMore"):
                return items
            offset += SUITEQL_PAGE_SIZE

    # ── reads ────────────────────────────────────────────────────────────

    def get_vendors(self) -> list[Vendor]:
        rows = self.suiteql(
            "SELECT id, entityid, companyname, email, subsidiary, currency "
            "FROM vendor WHERE isinactive = 'F'"
        )
        vendors = []
        for r in rows:
            email = r.get("email") or ""
            domains = [email.rsplit("@", 1)[-1].lower()] if "@" in email else []
            vendors.append(
                Vendor(
                    vendor_id=str(r.get("entityid") or r["id"]),
                    name=str(r.get("companyname") or r.get("entityid") or r["id"]),
                    subsidiary_id=str(r["subsidiary"]) if r.get("subsidiary") else None,
                    email_domains=domains,
                    currency=str(r.get("currency") or "USD"),
                )
            )
        return vendors

    def get_subsidiaries(self) -> list[Subsidiary]:
        rows = self.suiteql(
            "SELECT id, name, currency FROM subsidiary WHERE isinactive = 'F'"
        )
        return [
            Subsidiary(
                subsidiary_id=str(r["id"]),
                name=str(r["name"]),
                currency=str(r.get("currency") or "USD"),
            )
            for r in rows
        ]

    def get_open_purchase_orders(self) -> list[PurchaseOrder]:
        rows = self.suiteql(
            "SELECT t.id, t.tranid, t.entity, t.subsidiary, t.currency, "
            "  tl.id AS line_id, tl.memo, tl.expenseaccount, tl.department, "
            "  tl.foreignamount, tl.quantitybilled, tl.quantityshiprecv, tl.rate, "
            "  tl.custcol_service_start, tl.custcol_service_end "
            "FROM transaction t JOIN transactionline tl ON tl.transaction = t.id "
            "WHERE t.type = 'PurchOrd' AND t.status IN "
            "  ('PurchOrd:B', 'PurchOrd:D', 'PurchOrd:E', 'PurchOrd:F') "
            "AND tl.mainline = 'F'"
        )
        pos: dict[str, PurchaseOrder] = {}
        for r in rows:
            po_number = str(r["tranid"])
            po = pos.get(po_number)
            if po is None:
                po = PurchaseOrder(
                    po_number=po_number,
                    vendor_id=str(r["entity"]),
                    subsidiary_id=str(r["subsidiary"]),
                    currency=str(r.get("currency") or "USD"),
                )
                pos[po_number] = po
            amount = Decimal(str(r.get("foreignamount") or "0")).copy_abs()
            rate = Decimal(str(r.get("rate") or "0")).copy_abs()
            po.lines.append(
                PurchaseOrderLine(
                    line_id=str(r["line_id"]),
                    description=str(r.get("memo") or ""),
                    gl_account=str(r["expenseaccount"]) if r.get("expenseaccount") else None,
                    cost_center=str(r["department"]) if r.get("department") else None,
                    amount=amount,
                    billed_amount=(
                        Decimal(str(r.get("quantitybilled") or "0")) * rate
                    ).copy_abs(),
                    received_amount=(
                        Decimal(str(r.get("quantityshiprecv") or "0")) * rate
                    ).copy_abs(),
                    service_start=_opt_date(r.get("custcol_service_start")),
                    service_end=_opt_date(r.get("custcol_service_end")),
                )
            )
        return list(pos.values())

    def get_goods_receipts(self, start: dt.date, end: dt.date) -> list[GoodsReceipt]:
        rows = self.suiteql(
            "SELECT t.id, t.tranid, t.trandate, t.entity, "
            "  tl.id AS line_id, tl.foreignamount, "
            "  po.tranid AS po_number, pol.id AS po_line_id "
            "FROM transaction t "
            "JOIN transactionline tl ON tl.transaction = t.id AND tl.mainline = 'F' "
            "JOIN previoustransactionlinelink ptll ON ptll.nextdoc = t.id "
            "  AND ptll.nextline = tl.id "
            "JOIN transaction po ON po.id = ptll.previousdoc "
            "JOIN transactionline pol ON pol.transaction = po.id "
            "  AND pol.id = ptll.previousline "
            f"WHERE t.type = 'ItemRcpt' AND t.trandate BETWEEN "
            f"  TO_DATE('{start.isoformat()}', 'YYYY-MM-DD') AND "
            f"  TO_DATE('{end.isoformat()}', 'YYYY-MM-DD')"
        )
        return [
            GoodsReceipt(
                receipt_id=str(r["tranid"]),
                po_number=str(r["po_number"]),
                po_line_id=str(r["po_line_id"]),
                vendor_id=str(r["entity"]),
                received_date=_opt_date(r["trandate"]) or start,
                amount=Decimal(str(r.get("foreignamount") or "0")).copy_abs(),
            )
            for r in rows
        ]

    def get_vendor_bills(self, start: dt.date, end: dt.date) -> list[VendorBill]:
        rows = self.suiteql(
            "SELECT t.id, t.tranid, t.trandate, t.entity, t.foreigntotal, "
            "  t.currency, po.tranid AS po_number "
            "FROM transaction t "
            "LEFT JOIN previoustransactionlink ptl ON ptl.nextdoc = t.id "
            "LEFT JOIN transaction po ON po.id = ptl.previousdoc AND po.type = 'PurchOrd' "
            f"WHERE t.type = 'VendBill' AND t.trandate BETWEEN "
            f"  TO_DATE('{start.isoformat()}', 'YYYY-MM-DD') AND "
            f"  TO_DATE('{end.isoformat()}', 'YYYY-MM-DD')"
        )
        return [
            VendorBill(
                bill_id=str(r["id"]),
                vendor_id=str(r["entity"]),
                invoice_number=str(r["tranid"]),
                po_number=str(r["po_number"]) if r.get("po_number") else None,
                amount=Decimal(str(r.get("foreigntotal") or "0")).copy_abs(),
                currency=str(r.get("currency") or "USD"),
                bill_date=_opt_date(r["trandate"]) or start,
            )
            for r in rows
        ]

    def get_exchange_rate(self, currency: str, as_of: dt.date) -> Decimal:
        if currency == "USD":
            return Decimal("1")
        rows = self.suiteql(
            "SELECT exchangerate FROM currencyrate cr "
            "JOIN currency c ON c.id = cr.transactioncurrency "
            f"WHERE c.symbol = '{currency}' AND cr.effectivedate <= "
            f"TO_DATE('{as_of.isoformat()}', 'YYYY-MM-DD') "
            "ORDER BY cr.effectivedate DESC"
        )
        if not rows:
            raise UpstreamError(self.system, f"no exchange rate for {currency} as of {as_of}")
        return Decimal(str(rows[0]["exchangerate"]))

    # ── write-back ───────────────────────────────────────────────────────

    def post_journal_entry(self, je: JournalEntry) -> str:
        body = {
            "externalId": je.external_id,
            "tranDate": je.tran_date.isoformat(),
            "reversalDate": je.reversal_date.isoformat(),
            "reversalDefer": False,
            "subsidiary": {"id": je.subsidiary_id},
            "currency": {"refName": je.currency},
            "exchangeRate": str(je.exchange_rate),
            "memo": je.memo,
            "line": {
                "items": [
                    {
                        "account": {"id": je.debit_account},
                        "debit": str(je.amount),
                        "memo": je.memo,
                    },
                    {
                        "account": {"id": je.credit_account},
                        "credit": str(je.amount),
                        "memo": je.memo,
                    },
                ]
            },
        }
        try:
            resp = self.post_json("/record/v1/journalEntry", json=body)
        except UpstreamError as exc:
            # externalId dedupe: a duplicate post is rejected by NetSuite —
            # fetch the existing record instead of double-posting.
            if "409" in str(exc) or "already exists" in str(exc).lower():
                existing = self.get_json(f"/record/v1/journalEntry/eid:{je.external_id}")
                return str(existing["id"])
            raise
        location = resp.headers.get("Location", "")
        internal_id = location.rstrip("/").rsplit("/", 1)[-1] if location else ""
        if not internal_id:
            created = resp.json() if resp.content else {}
            internal_id = str(created.get("id", ""))
        if not internal_id:
            raise UpstreamError(self.system, "JE created but no internal id returned")
        log.info("netsuite.je_posted", external_id=je.external_id, netsuite_id=internal_id)
        return internal_id


def _opt_date(value: Any) -> dt.date | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        try:
            return dt.datetime.strptime(text, "%m/%d/%Y").date()
        except ValueError:
            return None
