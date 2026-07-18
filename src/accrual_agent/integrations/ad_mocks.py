"""Seeded Google Ads / Meta mocks with realistic restatement behavior.

Ad platforms restate spend for up to ~72h after period end. The mocks model
that: pulls dated inside the settle window return a slightly lower provisional
figure; later pulls return the settled final number. The lag-aware refresh in
engine/api_accruals re-pulls each cycle and adjusts the accrual.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal

from ..models import AdSpendRecord, SourceType

D = Decimal


def _default_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class _MockAdPlatform:
    platform: SourceType
    platform_name: str
    account_id: str
    provisional_spend: Decimal
    final_spend: Decimal

    def __init__(
        self,
        settle_hours: int = 72,
        now_provider: Callable[[], dt.datetime] = _default_now,
    ) -> None:
        self.settle_hours = settle_hours
        self.now_provider = now_provider
        self.fail_next_pull = False        # tests/demo can force the API-failure path

    def get_spend(self, start: dt.date, end: dt.date) -> list[AdSpendRecord]:
        if self.fail_next_pull:
            self.fail_next_pull = False
            from .base import UpstreamError

            raise UpstreamError(self.platform_name, "simulated outage (HTTP 503)")
        now = self.now_provider()
        settled_at = dt.datetime.combine(
            end, dt.time(23, 59, 59), tzinfo=dt.UTC
        ) + dt.timedelta(hours=self.settle_hours)
        spend = self.final_spend if now >= settled_at else self.provisional_spend
        return [
            AdSpendRecord(
                platform=self.platform,
                account_id=self.account_id,
                period_start=start,
                period_end=end,
                spend=spend,
                currency="USD",
                as_of=now,
            )
        ]


class MockGoogleAds(_MockAdPlatform):
    platform = SourceType.GOOGLE_ADS
    platform_name = "google_ads"
    account_id = "1234567890"
    provisional_spend = D("147910.22")
    final_spend = D("148270.45")


class MockMetaAds(_MockAdPlatform):
    platform = SourceType.META_ADS
    platform_name = "meta_ads"
    account_id = "act_123456789"
    provisional_spend = D("61890.55")
    final_spend = D("62540.10")
