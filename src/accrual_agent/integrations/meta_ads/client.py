"""Meta (Facebook) Marketing API client: ad-account spend by period.

Uses the Graph API insights edge at account level with a time_range; spend
comes back as a decimal string in the account currency.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import httpx

from ...config import Settings
from ...models import AdSpendRecord, SourceType
from ..base import BaseAPIClient, DataAnomalyError

GRAPH_API_VERSION = "v21.0"


class MetaAdsClient(BaseAPIClient):
    system = "meta_ads"
    platform_name = "meta_ads"

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        settings.require(
            {
                "META_ACCESS_TOKEN": settings.meta_access_token,
                "META_AD_ACCOUNT_IDS": settings.meta_ad_account_ids,
            },
            purpose="Meta Ads",
        )
        self.account_ids = [
            a.strip() for a in settings.meta_ad_account_ids.split(",") if a.strip()
        ]
        token = settings.meta_access_token

        def _auth(request: httpx.Request) -> httpx.Request:
            request.headers["Authorization"] = f"Bearer {token}"
            return request

        super().__init__(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}", auth_hook=_auth, **kwargs
        )

    def get_spend(self, start: dt.date, end: dt.date) -> list[AdSpendRecord]:
        records = []
        for account_id in self.account_ids:
            payload = self.get_json(
                f"/{account_id}/insights",
                params={
                    "fields": "spend,account_currency",
                    "time_range": json.dumps(
                        {"since": start.isoformat(), "until": end.isoformat()}
                    ),
                    "level": "account",
                },
            )
            rows: list[dict[str, Any]] = payload.get("data", [])
            spend = sum((Decimal(str(r.get("spend", "0"))) for r in rows), Decimal("0"))
            currency = rows[0].get("account_currency", "USD") if rows else "USD"
            if spend < 0:
                raise DataAnomalyError(
                    self.system, f"negative spend {spend} for account {account_id}"
                )
            records.append(
                AdSpendRecord(
                    platform=SourceType.META_ADS,
                    account_id=account_id,
                    period_start=start,
                    period_end=end,
                    spend=spend.quantize(Decimal("0.01")),
                    currency=currency,
                    as_of=dt.datetime.now(dt.UTC),
                )
            )
        return records
