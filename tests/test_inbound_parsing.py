import datetime as dt
from decimal import Decimal

from accrual_agent.comms.inbound import (
    HEURISTIC_CONFIDENCE_THRESHOLD,
    parse_reply_heuristic,
)
from accrual_agent.comms.llm_extractor import MockLLMExtractor


def test_clean_confirmation_parses_with_high_confidence():
    body = (
        "Hi, confirming $28,500.00 of hosting charges for June 2026.\n"
        "Invoice INV-8801 was issued and should reach you by July 8, 2026."
    )
    parsed = parse_reply_heuristic(body)
    assert parsed.confirmed_amount == Decimal("28500.00")
    assert parsed.currency == "USD"
    assert parsed.invoice_number == "INV-8801"
    assert parsed.expected_invoice_date == dt.date(2026, 7, 8)
    assert parsed.confirms_estimate is True
    assert parsed.confidence >= HEURISTIC_CONFIDENCE_THRESHOLD


def test_european_number_format_defers_to_fallback():
    body = "wir bestaetigen EUR 12.000,00 fuer Juni. Rechnung RE-2211 folgt."
    parsed = parse_reply_heuristic(body)
    # The heuristic must NOT misread 12.000,00 as 12.00 — it extracts nothing
    # and stays below the fallback threshold.
    assert parsed.confirmed_amount is None
    assert parsed.confidence < HEURISTIC_CONFIDENCE_THRESHOLD


def test_mock_llm_extractor_handles_eu_format():
    body = "wir bestaetigen EUR 12.000,00 fuer Juni. Rechnung RE-2211 folgt."
    parsed = MockLLMExtractor().extract(body)
    assert parsed is not None
    assert parsed.confirmed_amount == Decimal("12000.00")
    assert parsed.currency == "EUR"
    assert parsed.invoice_number == "RE-2211"
    assert parsed.method == "llm"


def test_prose_without_amount_is_low_confidence():
    parsed = parse_reply_heuristic(
        "Thanks for reaching out — let me check with the team and get back to you."
    )
    assert parsed.confirmed_amount is None
    assert parsed.confidence < HEURISTIC_CONFIDENCE_THRESHOLD


def test_iso_dates_and_bare_invoice_numbers():
    parsed = parse_reply_heuristic(
        "Total is USD 4,200.00, see INV-991AB. Expect the bill 2026-08-02."
    )
    assert parsed.confirmed_amount == Decimal("4200.00")
    assert parsed.invoice_number == "INV-991AB"
    assert parsed.expected_invoice_date == dt.date(2026, 8, 2)
