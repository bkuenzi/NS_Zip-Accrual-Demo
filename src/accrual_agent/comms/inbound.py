"""Inbound vendor-reply processing.

Pipeline per message: dedupe -> thread-match (reference token, then
In-Reply-To header) -> save attachments -> templated-reply parse (the
fill-in block our outbound email asks for) -> heuristic extraction -> LLM
fallback when confidence is low, second-pass verified -> hand the parsed
reply to the confirmation engine. A reply nothing can parse never
auto-confirms; it escalates instead.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

from ..logging_setup import get_logger
from ..models import AccrualLine, CommRecord, ParsedVendorReply
from ..register.repository import Repository
from .llm_extractor import LLMExtractor
from .mailer import InboundEmail

log = get_logger(__name__)

TOKEN_RE = re.compile(r"\[(ACR-[0-9]{4}-[0-9]{2}-[0-9]{4})\]")
AMOUNT_RE = re.compile(
    r"(?:(?P<cur>USD|EUR|GBP|CAD|AUD|\$|€|£)\s?)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2})"
)
INVOICE_RE = re.compile(r"\b(?:invoice|inv\.?|rechnung|#)\s*[:# ]?\s*([A-Z]{2,4}-?\d{3,}[A-Z0-9-]*)\b", re.IGNORECASE)
INVOICE_BARE_RE = re.compile(r"\b((?:INV|RE|RG|FA)-[A-Z0-9]{2,})\b")
CONFIRM_WORDS = ("confirm", "correct", "agree", "approved", "bestaetig", "bestätig")
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
LONG_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.IGNORECASE,
)
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
EU_FORMAT_RE = re.compile(r"\d{1,3}(?:\.\d{3})+,\d{2}")

HEURISTIC_CONFIDENCE_THRESHOLD = 0.6

# Fields of the fill-in reply block embedded in outbound confirmation emails.
TEMPLATE_FIELD_RE = re.compile(
    r"^[>\s]*(?P<label>AMOUNT|CURRENCY|DELIVERED PERCENT|INVOICE NUMBER|"
    r"EXPECTED INVOICE DATE)\b[^:\n]*:[ \t]*(?P<value>\S[^\n]*)$",
    re.MULTILINE | re.IGNORECASE,
)


def parse_reply_template(body: str) -> ParsedVendorReply | None:
    """Deterministic parse of the fill-in block; None when absent/unusable."""
    fields: dict[str, str] = {}
    for m in TEMPLATE_FIELD_RE.finditer(body):
        fields.setdefault(m.group("label").upper(), m.group("value").strip())
    raw_amount = fields.get("AMOUNT")
    if raw_amount is None:
        return None
    try:
        amount = Decimal(re.sub(r"[^\d.]", "", raw_amount))
    except ArithmeticError:
        return None
    currency = None
    if cur := fields.get("CURRENCY"):
        if m := re.search(r"\b([A-Z]{3})\b", cur.upper()):
            currency = m.group(1)
    delivered_pct = None
    if pct := fields.get("DELIVERED PERCENT"):
        try:
            delivered_pct = Decimal(re.sub(r"[^\d.]", "", pct))
        except ArithmeticError:
            pass
    invoice = None
    if inv := fields.get("INVOICE NUMBER"):
        if m := re.search(r"([A-Z]{2,4}-?\d{3,}[A-Z0-9-]*)", inv.upper()):
            invoice = m.group(1)
    eta = None
    if raw_eta := fields.get("EXPECTED INVOICE DATE"):
        if m := ISO_DATE_RE.search(raw_eta):
            eta = dt.date.fromisoformat(m.group(1))
    return ParsedVendorReply(
        confirmed_amount=amount,
        currency=currency,
        invoice_number=invoice,
        expected_invoice_date=eta,
        delivered_pct=delivered_pct,
        confirms_estimate=True,
        confidence=0.95,
        method="template",
        raw_excerpt=body[:160],
    )


def parse_reply_heuristic(body: str) -> ParsedVendorReply:
    """Deterministic first-pass extraction with a confidence score."""
    confidence = 0.0
    amounts: list[tuple[Decimal, str | None]] = []
    for m in AMOUNT_RE.finditer(body):
        cur = m.group("cur")
        amounts.append((
            Decimal(m.group("num").replace(",", "")),
            CURRENCY_SYMBOLS.get(cur, cur) if cur else None,
        ))
    # Only currency-tagged amounts are trustworthy; bare numbers match dates,
    # quantities, and zip codes far too often.
    tagged = [(a, c) for a, c in amounts if c is not None]
    amount: Decimal | None = None
    currency: str | None = None
    if EU_FORMAT_RE.search(body):
        # European decimal notation ("12.000,00") reads as a different number
        # under US rules — beyond this parser's competence, so extract nothing
        # and let the LLM fallback (or a human) handle it.
        tagged = []
    if len({a for a, _ in tagged}) == 1:
        amount, currency = tagged[0]
        confidence += 0.5
    elif tagged:
        amount, currency = tagged[0]      # multiple distinct figures: uncertain
        confidence += 0.2

    invoice = None
    m = INVOICE_RE.search(body) or INVOICE_BARE_RE.search(body)
    if m:
        invoice = m.group(1).upper()
        confidence += 0.2

    eta: dt.date | None = None
    if iso := ISO_DATE_RE.search(body):
        eta = dt.date.fromisoformat(iso.group(1))
        confidence += 0.15
    elif long := LONG_DATE_RE.search(body):
        month = dt.datetime.strptime(long.group(1)[:3], "%b").month
        eta = dt.date(int(long.group(3)), month, int(long.group(2)))
        confidence += 0.15

    lowered = body.lower()
    confirms = any(word in lowered for word in CONFIRM_WORDS) or None
    if confirms:
        confidence += 0.15

    return ParsedVendorReply(
        confirmed_amount=amount,
        currency=currency,
        invoice_number=invoice,
        expected_invoice_date=eta,
        confirms_estimate=confirms,
        confidence=round(min(confidence, 1.0), 2),
        method="heuristic",
        raw_excerpt=body[:160],
    )


class InboundService:
    def __init__(
        self,
        repo: Repository,
        mailer,                                  # SmtpImapMailer | MockMailer
        llm_extractor: LLMExtractor | None,
        artifacts_dir: str | Path,
    ) -> None:
        self.repo = repo
        self.mailer = mailer
        self.llm_extractor = llm_extractor
        self.artifacts_dir = Path(artifacts_dir)

    def poll(self) -> list[tuple[AccrualLine, ParsedVendorReply]]:
        """Fetch and process unseen mail; returns (line, parsed) pairs for the
        confirmation engine. Unmatched or unparseable messages are logged and
        surfaced, never guessed at."""
        results: list[tuple[AccrualLine, ParsedVendorReply]] = []
        for message in self.mailer.fetch_inbound():
            if self.repo.message_processed(message.message_id):
                continue
            self.repo.mark_message_processed(message.message_id)

            line = self._match_line(message)
            if line is None:
                log.warning(
                    "inbound.unmatched", message_id=message.message_id,
                    sender=message.sender, subject=message.subject,
                )
                self.repo.add_audit(
                    None, "accrual-agent", "inbound", "unmatched_message",
                    None, f"{message.sender}: {message.subject[:120]}",
                )
                continue

            attachment_paths = self._save_attachments(line, message)
            parsed = self._parse(message, attachment_paths)

            self.repo.add_comm(CommRecord(
                line_id=line.line_id,
                direction="inbound",
                stage="reply",
                sender=message.sender,
                recipient=None,
                subject=message.subject,
                message_id=message.message_id,
                in_reply_to=message.in_reply_to,
                body_preview=message.body[:400],
                attachment_paths=[str(p) for p in attachment_paths],
                sent_at=message.received_at,
                delivery="received",
            ))
            results.append((line, parsed))
        return results

    def _match_line(self, message: InboundEmail) -> AccrualLine | None:
        for text in (message.subject, message.body):
            if m := TOKEN_RE.search(text or ""):
                line = self.repo.get_line_by_token(f"[{m.group(1)}]")
                if line:
                    return line
        if message.in_reply_to:
            line_id = self.repo.outbound_message_ids().get(message.in_reply_to)
            if line_id:
                return self.repo.get_line(line_id)
        return None

    def _save_attachments(self, line: AccrualLine, message: InboundEmail) -> list[Path]:
        paths = []
        for filename, payload in message.attachments:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
            target = self.artifacts_dir / line.line_id / safe
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            paths.append(target)
            log.info("inbound.attachment_saved", line_id=line.line_id, path=str(target))
        return paths

    def _parse(
        self, message: InboundEmail, attachment_paths: list[Path]
    ) -> ParsedVendorReply:
        if templated := parse_reply_template(message.body):
            log.info(
                "inbound.template_parsed",
                amount=str(templated.confirmed_amount),
                delivered_pct=str(templated.delivered_pct),
            )
            return templated

        parsed = parse_reply_heuristic(message.body)
        if parsed.confidence >= HEURISTIC_CONFIDENCE_THRESHOLD:
            return parsed

        if self.llm_extractor is not None:
            llm_parsed = self.llm_extractor.extract(message.body)
            if llm_parsed is not None:
                # An LLM extraction only auto-confirms if an independent
                # second pass agrees with it; otherwise it's held for review.
                llm_parsed.verified = bool(
                    self.llm_extractor.verify(message.body, llm_parsed)
                )
                log.info(
                    "inbound.llm_fallback_used",
                    heuristic_confidence=parsed.confidence,
                    llm_amount=str(llm_parsed.confirmed_amount),
                    verified=llm_parsed.verified,
                )
                return llm_parsed

        # Attachment text can corroborate at reduced confidence — enough to
        # route the item to a human with context, never enough to auto-confirm.
        for path in attachment_paths:
            text = _extract_pdf_text(path)
            if not text:
                continue
            att_parsed = parse_reply_heuristic(text)
            if att_parsed.confirmed_amount is not None:
                att_parsed.confidence = round(min(att_parsed.confidence, 0.5), 2)
                att_parsed.method = "attachment"
                return att_parsed
        return parsed


def _extract_pdf_text(path: Path) -> str:
    if path.suffix.lower() != ".pdf":
        return ""
    try:
        import pdfplumber  # optional dependency: accrual-agent[pdf]
    except ImportError:
        return ""
    try:
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        log.warning("inbound.pdf_extract_failed", path=str(path), error=str(exc))
        return ""
