"""Real-client tests against mocked HTTP transport (respx).

The production clients are genuinely exercised: OAuth 1.0a signing, SuiteQL
pagination, retry-on-429/500, error mapping, and response parsing.
"""

import base64
import datetime as dt
from decimal import Decimal

import httpx
import pytest
import respx

from accrual_agent.config import Settings
from accrual_agent.integrations.base import (
    AuthError,
    BaseAPIClient,
    RateLimitedError,
    UpstreamError,
)
from accrual_agent.integrations.meta_ads.client import MetaAdsClient
from accrual_agent.integrations.netsuite.client import NetSuiteClient
from accrual_agent.integrations.netsuite.oauth1 import NetSuiteOAuth1Signer
from accrual_agent.integrations.zip_client.client import ZipClient


def live_settings(**overrides) -> Settings:
    base = Settings(_env_file=None)
    values = {
        "mode": "live",
        "netsuite_account_id": "1234567",
        "netsuite_consumer_key": "ck", "netsuite_consumer_secret": "cs",
        "netsuite_token_id": "tk", "netsuite_token_secret": "ts",
        "zip_api_key": "zk",
        "meta_access_token": "mt", "meta_ad_account_ids": "act_1",
    }
    values.update(overrides)
    return base.model_copy(update=values)


# ── OAuth 1.0a signing ───────────────────────────────────────────────────────


def make_signer(**kwargs) -> NetSuiteOAuth1Signer:
    return NetSuiteOAuth1Signer(
        "1234567", "ck", "cs", "tk", "ts",
        nonce_factory=lambda: "fixednonce", clock=lambda: 1_750_000_000, **kwargs
    )


def test_oauth1_header_structure_and_determinism():
    signer = make_signer()
    url = "https://1234567.suitetalk.api.netsuite.com/services/rest/query/v1/suiteql?limit=10"
    header1 = signer.authorization_header("POST", url)
    header2 = signer.authorization_header("POST", url)
    assert header1 == header2                       # deterministic given nonce+clock
    assert header1.startswith('OAuth realm="1234567"')
    for part in ("oauth_consumer_key=", "oauth_token=", "oauth_nonce=",
                 "oauth_signature_method=", "oauth_signature="):
        assert part in header1
    assert "HMAC-SHA256" in header1


def test_oauth1_signature_covers_query_params():
    signer = make_signer()
    base_url = "https://x.suitetalk.api.netsuite.com/services/rest/q"
    a = signer.authorization_header("GET", base_url + "?limit=10")
    b = signer.authorization_header("GET", base_url + "?limit=20")
    sig_a = a.split('oauth_signature="')[1].split('"')[0]
    sig_b = b.split('oauth_signature="')[1].split('"')[0]
    assert sig_a != sig_b


def test_oauth1_signature_is_valid_base64_hmac():
    header = make_signer().authorization_header("GET", "https://h.example/p")
    sig = header.split('oauth_signature="')[1].split('"')[0]
    import urllib.parse

    raw = base64.b64decode(urllib.parse.unquote(sig))
    assert len(raw) == 32  # HMAC-SHA256 digest length


# ── base client retry/error behavior ─────────────────────────────────────────


@respx.mock
def test_retry_on_500_then_success():
    route = respx.get("https://api.test/thing").mock(side_effect=[
        httpx.Response(500), httpx.Response(200, json={"ok": True}),
    ])
    client = BaseAPIClient("https://api.test", sleep=lambda s: None)
    assert client.get_json("/thing") == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_rate_limit_exhaustion_raises_typed_error():
    respx.get("https://api.test/thing").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"})
    )
    client = BaseAPIClient("https://api.test", max_retries=2, sleep=lambda s: None)
    with pytest.raises(RateLimitedError):
        client.get_json("/thing")


@respx.mock
def test_auth_errors_do_not_retry():
    route = respx.get("https://api.test/thing").mock(return_value=httpx.Response(401))
    client = BaseAPIClient("https://api.test", sleep=lambda s: None)
    with pytest.raises(AuthError):
        client.get_json("/thing")
    assert route.call_count == 1


@respx.mock
def test_4xx_maps_to_upstream_error():
    respx.get("https://api.test/thing").mock(return_value=httpx.Response(422, text="bad"))
    client = BaseAPIClient("https://api.test", sleep=lambda s: None)
    with pytest.raises(UpstreamError):
        client.get_json("/thing")


# ── NetSuite client ──────────────────────────────────────────────────────────


@respx.mock
def test_suiteql_pagination_accumulates_pages():
    url = "https://1234567.suitetalk.api.netsuite.com/services/rest/query/v1/suiteql"
    respx.post(url).mock(side_effect=[
        httpx.Response(200, json={"items": [{"id": 1}], "hasMore": True}),
        httpx.Response(200, json={"items": [{"id": 2}], "hasMore": False}),
    ])
    client = NetSuiteClient(live_settings(), sleep=lambda s: None)
    rows = client.suiteql("SELECT id FROM vendor")
    assert [r["id"] for r in rows] == [1, 2]


@respx.mock
def test_netsuite_requests_carry_oauth_header():
    url = "https://1234567.suitetalk.api.netsuite.com/services/rest/query/v1/suiteql"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json={"items": [], "hasMore": False})
    )
    NetSuiteClient(live_settings(), sleep=lambda s: None).suiteql("SELECT 1")
    auth = route.calls[0].request.headers["Authorization"]
    assert auth.startswith('OAuth realm="1234567"') and "oauth_signature=" in auth


@respx.mock
def test_je_post_parses_location_header():
    base = "https://1234567.suitetalk.api.netsuite.com/services/rest"
    respx.post(f"{base}/record/v1/journalEntry").mock(
        return_value=httpx.Response(
            204, headers={"Location": f"{base}/record/v1/journalEntry/4711"}
        )
    )
    from accrual_agent.models import JournalEntry

    je = JournalEntry(
        line_id="ACR-2026-06-0001", external_id="ACRJE-x", tran_date=dt.date(2026, 6, 30),
        reversal_date=dt.date(2026, 7, 1), subsidiary_id="1", debit_account="6210",
        credit_account="2150", amount=Decimal("100.00"), currency="USD",
        exchange_rate=Decimal("1"), memo="test",
    )
    client = NetSuiteClient(live_settings(), sleep=lambda s: None)
    assert client.post_journal_entry(je) == "4711"


def test_netsuite_requires_credentials():
    from accrual_agent.config import ConfigError

    with pytest.raises(ConfigError, match="NETSUITE_TOKEN_ID"):
        NetSuiteClient(live_settings(netsuite_token_id=""))


# ── Zip client (read-only) ───────────────────────────────────────────────────


@respx.mock
def test_zip_pagination_and_parsing():
    respx.get("https://api.ziphq.com/v1/requisitions").mock(side_effect=[
        httpx.Response(200, json={
            "data": [{
                "id": "ZR-1", "vendor": {"id": "v1", "external_id": "V-A", "name": "A"},
                "business_unit": {"code": "BU-US"}, "committed_amount": "1500.00",
                "currency": "USD", "approved_at": "2026-06-03T12:00:00Z",
                "service_start": "2026-06-01", "service_end": "2026-06-30",
            }],
            "next_cursor": "c2",
        }),
        httpx.Response(200, json={"data": [], "next_cursor": None}),
    ])
    client = ZipClient(live_settings(), sleep=lambda s: None)
    reqs = client.get_approved_requisitions(dt.date(2026, 6, 1), dt.date(2026, 6, 30))
    assert len(reqs) == 1
    assert reqs[0].vendor_id == "V-A"
    assert reqs[0].committed_amount == Decimal("1500.00")


def test_zip_adapter_has_no_write_surface():
    """Read-only by construction: no method on the Zip client can mutate Zip."""
    client_methods = {
        name for name in dir(ZipClient)
        if not name.startswith("_") and callable(getattr(ZipClient, name))
    }
    base_methods = set(dir(BaseAPIClient))
    zip_specific = client_methods - base_methods
    assert zip_specific == {"get_approved_requisitions"}


# ── Meta client ──────────────────────────────────────────────────────────────


@respx.mock
def test_meta_spend_parsing():
    respx.get("https://graph.facebook.com/v21.0/act_1/insights").mock(
        return_value=httpx.Response(200, json={
            "data": [{"spend": "62540.10", "account_currency": "USD"}]
        })
    )
    client = MetaAdsClient(live_settings(), sleep=lambda s: None)
    records = client.get_spend(dt.date(2026, 6, 1), dt.date(2026, 6, 30))
    assert records[0].spend == Decimal("62540.10")
    assert records[0].account_id == "act_1"
