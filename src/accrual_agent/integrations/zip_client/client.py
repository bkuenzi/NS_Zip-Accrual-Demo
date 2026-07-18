"""Zip (ziphq.com) API client — read-only by construction.

Pulls approved requisitions / vendor engagements and their committed spend so
the engine can surface non-PO uninvoiced gaps. This class deliberately exposes
no write methods: the agent never modifies Zip requisitions.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import httpx

from ...config import Settings
from ...models import ZipRequisition
from ..base import BaseAPIClient


class ZipClient(BaseAPIClient):
    system = "zip"

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        settings.require({"ZIP_API_KEY": settings.zip_api_key}, purpose="Zip")

        def _auth(request: httpx.Request) -> httpx.Request:
            request.headers["Authorization"] = f"Bearer {settings.zip_api_key}"
            return request

        super().__init__(settings.zip_api_base_url, auth_hook=_auth, **kwargs)

    def get_approved_requisitions(
        self, start: dt.date, end: dt.date
    ) -> list[ZipRequisition]:
        requisitions: list[ZipRequisition] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "status": "approved",
                "approved_after": start.isoformat(),
                "approved_before": end.isoformat(),
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor
            payload = self.get_json("/v1/requisitions", params=params)
            for item in payload.get("data", []):
                requisitions.append(_parse_requisition(item))
            cursor = payload.get("next_cursor")
            if not cursor:
                return requisitions


def _parse_requisition(item: dict[str, Any]) -> ZipRequisition:
    return ZipRequisition(
        requisition_id=str(item["id"]),
        vendor_id=str(item.get("vendor", {}).get("external_id") or item["vendor"]["id"]),
        vendor_name=str(item.get("vendor", {}).get("name", "")),
        business_unit=str(item.get("business_unit", {}).get("code", "")),
        committed_amount=Decimal(str(item.get("committed_amount", "0"))),
        currency=str(item.get("currency", "USD")),
        approved_date=dt.date.fromisoformat(str(item["approved_at"])[:10]),
        service_start=_opt_date(item.get("service_start")),
        service_end=_opt_date(item.get("service_end")),
        po_number=item.get("po_number") or None,
        gl_account=str(item["gl_account"]) if item.get("gl_account") else None,
        cost_center=str(item["cost_center"]) if item.get("cost_center") else None,
    )


def _opt_date(value: Any) -> dt.date | None:
    return dt.date.fromisoformat(str(value)[:10]) if value else None
