"""OAuth 1.0a request signing for NetSuite Token-Based Authentication.

NetSuite SuiteTalk REST requires each request to carry an OAuth 1.0a
Authorization header signed with HMAC-SHA256 over consumer + token secrets
(RFC 5849, with NetSuite's realm=ACCOUNT_ID extension).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote, urlsplit

import httpx


def _pct(value: str) -> str:
    return quote(value, safe="~")


class NetSuiteOAuth1Signer:
    def __init__(
        self,
        account_id: str,
        consumer_key: str,
        consumer_secret: str,
        token_id: str,
        token_secret: str,
        *,
        nonce_factory=lambda: secrets.token_hex(16),
        clock=time.time,
    ) -> None:
        self.account_id = account_id
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.token_id = token_id
        self.token_secret = token_secret
        self._nonce_factory = nonce_factory
        self._clock = clock

    def __call__(self, request: httpx.Request) -> httpx.Request:
        request.headers["Authorization"] = self.authorization_header(
            request.method, str(request.url)
        )
        return request

    def authorization_header(self, method: str, url: str) -> str:
        split = urlsplit(url)
        base_uri = f"{split.scheme}://{split.netloc}{split.path}"

        oauth_params = {
            "oauth_consumer_key": self.consumer_key,
            "oauth_token": self.token_id,
            "oauth_signature_method": "HMAC-SHA256",
            "oauth_timestamp": str(int(self._clock())),
            "oauth_nonce": self._nonce_factory(),
            "oauth_version": "1.0",
        }

        # Signature base string covers oauth params + query-string params.
        all_params: list[tuple[str, str]] = list(oauth_params.items())
        if split.query:
            for pair in split.query.split("&"):
                key, _, value = pair.partition("=")
                all_params.append((_unquote(key), _unquote(value)))
        encoded = sorted((_pct(k), _pct(v)) for k, v in all_params)
        param_string = "&".join(f"{k}={v}" for k, v in encoded)
        base_string = "&".join(
            (method.upper(), _pct(base_uri), _pct(param_string))
        )
        signing_key = f"{_pct(self.consumer_secret)}&{_pct(self.token_secret)}"
        digest = hmac.new(
            signing_key.encode(), base_string.encode(), hashlib.sha256
        ).digest()
        signature = base64.b64encode(digest).decode()

        header_params = dict(oauth_params, oauth_signature=signature)
        header = ", ".join(
            f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(header_params.items())
        )
        return f'OAuth realm="{self.account_id}", {header}'


def _unquote(value: str) -> str:
    from urllib.parse import unquote_plus

    return unquote_plus(value)
