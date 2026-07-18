"""Shared HTTP plumbing for all real API clients.

Every integration gets: bearer/custom auth hooks, exponential-backoff retries
with Retry-After handling, a typed error taxonomy, and structured logging of
each call.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from ..logging_setup import get_logger

log = get_logger(__name__)


class IntegrationError(RuntimeError):
    """Base class for anything a client can raise; carries the system name."""

    def __init__(self, system: str, message: str):
        self.system = system
        super().__init__(f"[{system}] {message}")


class AuthError(IntegrationError):
    pass


class RateLimitedError(IntegrationError):
    pass


class UpstreamError(IntegrationError):
    pass


class DataAnomalyError(IntegrationError):
    """Response parsed but the data fails sanity checks (negative spend, etc.)."""


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class BaseAPIClient:
    system = "generic"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 4,
        backoff_base_s: float = 1.0,
        auth_hook: Callable[[httpx.Request], httpx.Request] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.auth_hook = auth_hook
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        attempt = 0
        while True:
            req = self._client.build_request(
                method, path, params=params, json=json, headers=headers
            )
            if self.auth_hook is not None:
                req = self.auth_hook(req)
            started = time.monotonic()
            try:
                resp = self._client.send(req)
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    self._backoff(attempt, reason=f"transport error: {exc}")
                    attempt += 1
                    continue
                raise UpstreamError(self.system, f"transport failure: {exc}") from exc

            log.info(
                "api.call",
                system=self.system, method=method, path=path,
                status=resp.status_code,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )

            if resp.status_code in (401, 403):
                raise AuthError(
                    self.system,
                    f"{resp.status_code} on {method} {path}: check credentials/permissions",
                )
            if resp.status_code in RETRYABLE_STATUS:
                if attempt < self.max_retries:
                    retry_after = _retry_after_seconds(resp)
                    self._backoff(attempt, reason=f"HTTP {resp.status_code}",
                                  minimum=retry_after)
                    attempt += 1
                    continue
                if resp.status_code == 429:
                    raise RateLimitedError(
                        self.system, f"rate limited on {method} {path} after retries"
                    )
                raise UpstreamError(
                    self.system,
                    f"HTTP {resp.status_code} on {method} {path} after retries",
                )
            if resp.status_code >= 400:
                raise UpstreamError(
                    self.system,
                    f"HTTP {resp.status_code} on {method} {path}: {resp.text[:300]}",
                )
            return resp

    def get_json(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs).json()

    def post_json(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def _backoff(self, attempt: int, *, reason: str, minimum: float | None = None) -> None:
        delay = self.backoff_base_s * (2**attempt)
        if minimum:
            delay = max(delay, minimum)
        log.warning(
            "api.retry", system=self.system, attempt=attempt + 1,
            delay_s=round(delay, 2), reason=reason,
        )
        self._sleep(delay)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
