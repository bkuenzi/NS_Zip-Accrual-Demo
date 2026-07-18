"""Google Ads API client: campaign spend by date range via searchStream.

Auth: OAuth2 refresh-token flow (access token minted per run) plus the
developer-token header the Google Ads API requires. cost_micros is summed per
ad account across the requested range.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import httpx

from ...config import Settings
from ...models import AdSpendRecord, SourceType
from ..base import AuthError, BaseAPIClient, DataAnomalyError

GOOGLE_ADS_API_VERSION = "v18"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleAdsClient(BaseAPIClient):
    system = "google_ads"
    platform_name = "google_ads"

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        settings.require(
            {
                "GOOGLE_ADS_DEVELOPER_TOKEN": settings.google_ads_developer_token,
                "GOOGLE_ADS_OAUTH_CLIENT_ID": settings.google_ads_oauth_client_id,
                "GOOGLE_ADS_OAUTH_CLIENT_SECRET": settings.google_ads_oauth_client_secret,
                "GOOGLE_ADS_REFRESH_TOKEN": settings.google_ads_refresh_token,
                "GOOGLE_ADS_CUSTOMER_IDS": settings.google_ads_customer_ids,
            },
            purpose="Google Ads",
        )
        self.settings = settings
        self.customer_ids = [
            c.strip() for c in settings.google_ads_customer_ids.split(",") if c.strip()
        ]
        self._access_token: str | None = None
        super().__init__(
            f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}", **kwargs
        )

    def _ensure_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        resp = httpx.post(
            OAUTH_TOKEN_URL,
            data={
                "client_id": self.settings.google_ads_oauth_client_id,
                "client_secret": self.settings.google_ads_oauth_client_secret,
                "refresh_token": self.settings.google_ads_refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise AuthError(self.system, f"OAuth token refresh failed: {resp.text[:200]}")
        self._access_token = resp.json()["access_token"]
        return self._access_token

    def get_spend(self, start: dt.date, end: dt.date) -> list[AdSpendRecord]:
        token = self._ensure_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "developer-token": self.settings.google_ads_developer_token,
        }
        if self.settings.google_ads_login_customer_id:
            headers["login-customer-id"] = self.settings.google_ads_login_customer_id

        records = []
        query = (
            "SELECT metrics.cost_micros, customer.currency_code, segments.date "
            "FROM customer "
            f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
        )
        for customer_id in self.customer_ids:
            chunks = self.post_json(
                f"/customers/{customer_id}/googleAds:searchStream",
                json={"query": query},
                headers=headers,
            ).json()
            total_micros = 0
            currency = "USD"
            for chunk in chunks if isinstance(chunks, list) else [chunks]:
                for row in chunk.get("results", []):
                    total_micros += int(row.get("metrics", {}).get("costMicros", 0))
                    currency = row.get("customer", {}).get("currencyCode", currency)
            spend = (Decimal(total_micros) / Decimal(1_000_000)).quantize(Decimal("0.01"))
            if spend < 0:
                raise DataAnomalyError(
                    self.system, f"negative spend {spend} for account {customer_id}"
                )
            records.append(
                AdSpendRecord(
                    platform=SourceType.GOOGLE_ADS,
                    account_id=customer_id,
                    period_start=start,
                    period_end=end,
                    spend=spend,
                    currency=currency,
                    as_of=dt.datetime.now(dt.UTC),
                )
            )
        return records
