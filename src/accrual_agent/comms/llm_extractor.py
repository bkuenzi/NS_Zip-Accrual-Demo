"""LLM fallback for vendor-reply extraction when heuristics are unsure.

The Claude API is asked for a strict-JSON extraction of confirmed amount,
invoice number, and expected invoice date. Model is configurable
(ACCRUAL_LLM_MODEL, default claude-haiku-4-5). Mock mode uses a deterministic
stand-in that understands the formats heuristics miss (e.g. European decimal
notation), keeping the fallback path exercised without an API key.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from typing import Protocol

from ..config import Settings
from ..logging_setup import get_logger
from ..models import ParsedVendorReply

log = get_logger(__name__)

EXTRACTION_PROMPT = """You are extracting structured accrual-confirmation data \
from a vendor's email reply to an accounting team.

Reply with ONLY a JSON object, no prose, with these keys:
  confirmed_amount: number or null — the amount the vendor confirms as incurred/invoiced
  currency: 3-letter code or null
  invoice_number: string or null
  expected_invoice_date: YYYY-MM-DD or null
  confirms_estimate: true/false/null — does the vendor agree with the estimate?

Vendor email:
---
{body}
---"""

VERIFICATION_PROMPT = """You are double-checking a colleague's extraction from \
a vendor's email reply to an accounting team. Read the email independently and \
decide whether the extraction below is faithful to it.

Extraction under review:
  confirmed_amount: {amount}
  currency: {currency}

Reply with ONLY a JSON object: {{"agrees": true}} if the email genuinely states \
that amount (and currency, if given) as the confirmed/incurred figure, \
{{"agrees": false}} otherwise. When unsure, answer false.

Vendor email:
---
{body}
---"""


class LLMExtractor(Protocol):
    def extract(self, body: str) -> ParsedVendorReply | None: ...

    def verify(self, body: str, parsed: ParsedVendorReply) -> bool:
        """Independent second pass: does the extraction match the email?
        Must fail closed — any doubt or error returns False."""
        ...


class AnthropicExtractor:
    def __init__(self, settings: Settings):
        settings.require(
            {"ANTHROPIC_API_KEY": settings.anthropic_api_key}, purpose="LLM extraction"
        )
        import anthropic  # optional dependency: accrual-agent[llm]

        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.llm_model

    def extract(self, body: str) -> ParsedVendorReply | None:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": EXTRACTION_PROMPT.format(body=body[:4000]),
            }],
        )
        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        try:
            payload = json.loads(_strip_fences(text))
        except json.JSONDecodeError:
            log.warning("llm_extractor.bad_json", raw=text[:200])
            return None
        return _payload_to_reply(payload, body)

    def verify(self, body: str, parsed: ParsedVendorReply) -> bool:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=50,
                messages=[{
                    "role": "user",
                    "content": VERIFICATION_PROMPT.format(
                        amount=parsed.confirmed_amount,
                        currency=parsed.currency or "unspecified",
                        body=body[:4000],
                    ),
                }],
            )
            text = "".join(
                block.text for block in response.content if block.type == "text"
            ).strip()
            payload = json.loads(_strip_fences(text))
            agrees = payload.get("agrees") is True
        except Exception as exc:  # noqa: BLE001 — verification must fail closed
            log.warning("llm_extractor.verify_failed", error=str(exc))
            return False
        log.info("llm_extractor.verified", agrees=agrees)
        return agrees


class MockLLMExtractor:
    """Deterministic stand-in for the Claude call in mock mode.

    Handles what the heuristics deliberately don't — European number formats
    like "EUR 12.000,00" — so the demo genuinely routes through the fallback.
    """

    def extract(self, body: str) -> ParsedVendorReply | None:
        eu_match = re.search(
            r"(EUR|GBP|CHF|SEK|DKK|PLN)\s*([\d.]+,\d{2})", body, re.IGNORECASE
        )
        us_match = re.search(r"[$]\s*([\d,]+(?:\.\d{2})?)", body)
        amount = currency = None
        if eu_match:
            currency = eu_match.group(1).upper()
            amount = Decimal(eu_match.group(2).replace(".", "").replace(",", "."))
        elif us_match:
            currency = "USD"
            amount = Decimal(us_match.group(1).replace(",", ""))
        invoice = re.search(r"\b((?:INV|RE|RG|FA)[-–]?[A-Z0-9]{2,})\b", body)
        if amount is None and invoice is None:
            return None
        return ParsedVendorReply(
            confirmed_amount=amount,
            currency=currency,
            invoice_number=invoice.group(1) if invoice else None,
            confirms_estimate="besta" in body.lower() or "confirm" in body.lower(),
            confidence=0.9,
            method="llm",
            raw_excerpt=body[:160],
        )

    def verify(self, body: str, parsed: ParsedVendorReply) -> bool:
        """Second pass in mock mode: re-extract and require agreement."""
        second = self.extract(body)
        return (
            second is not None
            and second.confirmed_amount == parsed.confirmed_amount
            and (second.currency or None) == (parsed.currency or None)
        )


def build_extractor(settings: Settings) -> LLMExtractor | None:
    if settings.mode == "mock":
        return MockLLMExtractor()
    if settings.anthropic_api_key:
        return AnthropicExtractor(settings)
    return None


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text


def _payload_to_reply(payload: dict, body: str) -> ParsedVendorReply | None:
    try:
        amount = payload.get("confirmed_amount")
        eta = payload.get("expected_invoice_date")
        return ParsedVendorReply(
            confirmed_amount=Decimal(str(amount)) if amount is not None else None,
            currency=payload.get("currency"),
            invoice_number=payload.get("invoice_number"),
            expected_invoice_date=dt.date.fromisoformat(eta) if eta else None,
            confirms_estimate=payload.get("confirms_estimate"),
            confidence=0.9,
            method="llm",
            raw_excerpt=body[:160],
        )
    except (ValueError, ArithmeticError):
        log.warning("llm_extractor.bad_payload", payload=str(payload)[:200])
        return None
