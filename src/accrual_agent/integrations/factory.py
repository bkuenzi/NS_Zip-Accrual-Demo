"""Adapter factory: one place that decides mock vs live per settings.mode."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Settings
from .ad_mocks import MockGoogleAds, MockMetaAds
from .interfaces import AdPlatformAdapter, NetSuiteAdapter, ZipAdapter


@dataclass
class AdapterSet:
    netsuite: NetSuiteAdapter
    zip: ZipAdapter
    ad_platforms: list[AdPlatformAdapter] = field(default_factory=list)


def build_adapters(
    settings: Settings,
    now_provider: Callable[[], dt.datetime] | None = None,
) -> AdapterSet:
    """`now_provider` lets the demo simulate close days; live mode ignores it."""
    if settings.mode == "mock":
        from .netsuite.mock import MockNetSuite
        from .zip_client.mock import MockZip

        kwargs = {"settle_hours": settings.ad_settle_hours}
        if now_provider is not None:
            kwargs["now_provider"] = now_provider
        return AdapterSet(
            netsuite=MockNetSuite(),
            zip=MockZip(),
            ad_platforms=[MockGoogleAds(**kwargs), MockMetaAds(**kwargs)],
        )

    from .google_ads.client import GoogleAdsClient
    from .meta_ads.client import MetaAdsClient
    from .netsuite.client import NetSuiteClient
    from .zip_client.client import ZipClient

    ad_platforms: list[AdPlatformAdapter] = []
    if settings.google_ads_customer_ids:
        ad_platforms.append(GoogleAdsClient(settings))
    if settings.meta_ad_account_ids:
        ad_platforms.append(MetaAdsClient(settings))
    return AdapterSet(
        netsuite=NetSuiteClient(settings),
        zip=ZipClient(settings),
        ad_platforms=ad_platforms,
    )
