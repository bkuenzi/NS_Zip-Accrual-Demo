"""Email transport: SMTP send + IMAP inbound poll, with send-safety modes.

outbound_mode:
  dry_run — render and log, send nothing (default; forced in mock mode)
  sandbox — deliver only to allowlisted domains, or redirect everything to a
            sandbox address
  live    — real sends (explicit env opt-in)
"""

from __future__ import annotations

import datetime as dt
import email
import email.message
import imaplib
import smtplib
import uuid
from dataclasses import dataclass, field

from ..config import Settings
from ..logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class OutboundEmail:
    to: str
    subject: str
    body: str
    message_id: str = ""
    in_reply_to: str | None = None

    def __post_init__(self) -> None:
        if not self.message_id:
            self.message_id = f"<{uuid.uuid4().hex}@accrual-agent>"


@dataclass
class InboundEmail:
    message_id: str
    sender: str
    subject: str
    body: str
    in_reply_to: str | None = None
    received_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    attachments: list[tuple[str, bytes]] = field(default_factory=list)


class SmtpImapMailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    # ── outbound ─────────────────────────────────────────────────────────

    def send(self, message: OutboundEmail) -> str:
        """Send per the effective outbound mode; returns the delivery label."""
        mode = self.settings.effective_outbound_mode
        target = message.to

        if mode == "dry_run":
            log.info("mail.dry_run", to=target, subject=message.subject,
                     message_id=message.message_id)
            return "dry_run"

        if mode == "sandbox":
            allowed = {
                d.strip().lower()
                for d in self.settings.sandbox_allowed_domains.split(",") if d.strip()
            }
            domain = target.rsplit("@", 1)[-1].lower()
            if self.settings.sandbox_redirect:
                target = self.settings.sandbox_redirect
            elif domain not in allowed:
                log.info("mail.sandbox_suppressed", to=message.to, domain=domain)
                return "sandbox_suppressed"

        self.settings.require(
            {"SMTP_HOST": self.settings.smtp_host,
             "SMTP_USERNAME": self.settings.smtp_username,
             "SMTP_PASSWORD": self.settings.smtp_password},
            purpose="SMTP send",
        )
        msg = email.message.EmailMessage()
        msg["From"] = self.settings.mailbox_address
        msg["Reply-To"] = self.settings.mailbox_address
        msg["To"] = target
        msg["Subject"] = message.subject
        msg["Message-ID"] = message.message_id
        if message.in_reply_to:
            msg["In-Reply-To"] = message.in_reply_to
        msg.set_content(message.body)

        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port) as smtp:
            smtp.starttls()
            smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            smtp.send_message(msg)
        log.info("mail.sent", to=target, subject=message.subject, mode=mode)
        return mode

    # ── inbound ──────────────────────────────────────────────────────────

    def fetch_inbound(self) -> list[InboundEmail]:
        self.settings.require(
            {"IMAP_HOST": self.settings.imap_host,
             "IMAP_USERNAME": self.settings.imap_username,
             "IMAP_PASSWORD": self.settings.imap_password},
            purpose="IMAP poll",
        )
        results: list[InboundEmail] = []
        with imaplib.IMAP4_SSL(self.settings.imap_host, self.settings.imap_port) as imap:
            imap.login(self.settings.imap_username, self.settings.imap_password)
            imap.select("INBOX")
            _, data = imap.search(None, "UNSEEN")
            for num in data[0].split():
                _, msg_data = imap.fetch(num, "(RFC822)")
                if not msg_data or msg_data[0] is None:
                    continue
                raw = msg_data[0][1]
                parsed = email.message_from_bytes(raw)
                results.append(_to_inbound(parsed))
        log.info("mail.inbound_fetched", count=len(results))
        return results


def _to_inbound(msg: email.message.Message) -> InboundEmail:
    body_parts: list[str] = []
    attachments: list[tuple[str, bytes]] = []
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition") or "")
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True) or b""
        if "attachment" in disposition:
            attachments.append((part.get_filename() or "attachment.bin", payload))
        elif part.get_content_type() == "text/plain":
            charset = part.get_content_charset() or "utf-8"
            body_parts.append(payload.decode(charset, errors="replace"))
    return InboundEmail(
        message_id=msg.get("Message-ID", f"<missing-{uuid.uuid4().hex}>"),
        sender=email.utils.parseaddr(msg.get("From", ""))[1],
        subject=msg.get("Subject", ""),
        body="\n".join(body_parts),
        in_reply_to=msg.get("In-Reply-To"),
        attachments=attachments,
    )


class MockMailer:
    """Simulated vendor mailbox for mock mode and the scripted demo.

    Outbound messages are recorded (never delivered). Inbound replies are
    synthesized from per-vendor fixtures: once the initial request for a
    vendor has gone out and the close day reaches the fixture's reply day,
    the vendor "replies" to that thread.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.sent: list[OutboundEmail] = []
        self.close_day = 0
        # vendor_id -> (reply_day, subject_suffix, body, attachments)
        self.reply_fixtures: dict[str, tuple[int, str, list[tuple[str, bytes]]]] = {}
        self._sent_by_vendor: dict[str, OutboundEmail] = {}
        self._vendor_of_message: dict[str, str] = {}

    def register_outbound_vendor(self, message_id: str, vendor_id: str) -> None:
        self._vendor_of_message[message_id] = vendor_id

    def hydrate(self, prior_sends: list[tuple[str, str, str, str]]) -> None:
        """Rebuild thread state from the comm log across CLI invocations.

        prior_sends: (vendor_id, message_id, recipient, subject) tuples for
        outbound messages recorded in earlier runs.
        """
        for vendor_id, message_id, recipient, subject in prior_sends:
            self._vendor_of_message[message_id] = vendor_id
            self._sent_by_vendor.setdefault(
                vendor_id,
                OutboundEmail(to=recipient, subject=subject, body="", message_id=message_id),
            )

    def send(self, message: OutboundEmail) -> str:
        self.sent.append(message)
        vendor_id = self._vendor_of_message.get(message.message_id)
        if vendor_id and vendor_id not in self._sent_by_vendor:
            self._sent_by_vendor[vendor_id] = message
        log.info("mock_mail.recorded", to=message.to, subject=message.subject)
        return "dry_run"

    def fetch_inbound(self) -> list[InboundEmail]:
        replies = []
        for vendor_id, (reply_day, body, attachments) in self.reply_fixtures.items():
            initial = self._sent_by_vendor.get(vendor_id)
            if initial is None or self.close_day < reply_day:
                continue
            replies.append(
                InboundEmail(
                    message_id=f"<reply-{vendor_id.lower()}@vendor.example>",
                    sender=initial.to,
                    subject=f"Re: {initial.subject}",
                    body=body,
                    in_reply_to=initial.message_id,
                    attachments=list(attachments),
                )
            )
        return replies


DEMO_REPLY_FIXTURES: dict[str, tuple[int, str, list[tuple[str, bytes]]]] = {
    # Clean confirmation -> heuristic parse, within threshold, confirms
    "V-ACME": (
        3,
        "Hi,\n\nConfirming $28,500.00 of hosting charges for June 2026.\n"
        "Invoice INV-8801 has been issued and should reach your AP inbox by "
        "July 8, 2026.\n\nBest,\nPriya\nAcme Cloud Services",
        [],
    ),
    # European number format + German phrasing -> heuristics fail -> LLM fallback
    "V-ZETA": (
        5,
        "Guten Tag,\n\nhiermit bestaetigen wir erbrachte Leistungen in Hoehe von "
        "EUR 12.000,00 fuer Juni 2026. Die Rechnung RE-2211 folgt Anfang Juli.\n\n"
        "Mit freundlichen Gruessen\nJonas Keller\nZeta GmbH",
        [("RE-2211-vorschau.pdf", b"%PDF-1.4 placeholder preview")],
    ),
    # Internal budget owner (routing: internal for V-GAMMA) disputes beyond
    # the (10% for GL 6620) threshold -> held for review — "the catch"
    "V-GAMMA": (
        7,
        "Hi,\n\nChecked with the project team — Gamma has actually burned "
        "$17,800.00 of the June engagement, not the $15,000.00 committed on the "
        "req. Scope grew mid-month; their final invoice should land next week.\n\n"
        "Morgan Patel\nEngagement owner, Gamma Consulting account",
        [],
    ),
    # Confirms a higher true-up -> variance breach at the default 5% -> held
    "V-THETA": (
        7,
        "Hi team,\n\nThe June usage true-up came to $33,500.00. Invoice INV-5150 "
        "was issued on July 9, 2026 and is on its way.\n\nAde Okafor\nTheta Software",
        [],
    ),
    # Fills in the structured reply block (deterministic template parse) and
    # states the delivered share — replaces straight-line proration as basis
    "V-ETA": (
        5,
        "Hi,\n\nFilled in below — this matches our side.\n\n"
        "    AMOUNT: 19,565.22\n"
        "    CURRENCY: USD\n"
        "    DELIVERED PERCENT (services only — share of the work delivered): 32.6\n"
        "    INVOICE NUMBER (if issued):\n"
        "    EXPECTED INVOICE DATE (YYYY-MM-DD): 2026-07-15\n\n"
        "Invoicing monthly per the SOW.\n\nDana Whitfield\nEta Media Production",
        [],
    ),
    # V-BETA intentionally never replies -> full reminder ladder + escalation
}


MVP_REPLY_FIXTURES: dict[str, tuple[int, str, list[tuple[str, bytes]]]] = {
    # SeatGeek dataset (mvp profile). Amounts tie to the identification engine's
    # estimates so each reply exercises a specific confirmation path.
    # AWS — clean confirmation within threshold -> heuristic confirm
    "V-AWS": (
        3,
        "Hi,\n\nConfirming $487,500.00 of production infrastructure usage for "
        "June 2026 (PO-2101). The AWS June invoice will post to your AP inbox by "
        "2026-07-06.\n\nThanks,\nJordan\nAWS Billing",
        [],
    ),
    # The Trade Desk — Zip committed spend, clean confirm
    "V-TTD": (
        3,
        "Hello,\n\nWe confirm $420,000.00 of managed programmatic spend for the "
        "June 2026 flight. Invoice to follow by 2026-07-08.\n\nBest,\nMorgan\n"
        "The Trade Desk",
        [],
    ),
    # iHeartMedia — prorated service-PO estimate, agrees within threshold
    "V-IHEART": (
        5,
        "Hi team,\n\nThat aligns with our records — we confirm $587,000.00 for the "
        "June portion of the national audio brand campaign (PO-2104). Monthly "
        "billing per the IO; June's invoice lands 2026-07-15.\n\nCasey\niHeartMedia",
        [],
    ),
    # impact.com — affiliate Zip spend, clean confirm
    "V-IMPACT": (
        5,
        "Hello,\n\nConfirmed: $185,000.00 in affiliate and partner payouts for "
        "June 2026. Statement will be issued 2026-07-10.\n\nRegards,\nTaylor\n"
        "impact.com",
        [],
    ),
    # Stormfactory (GBP, sub 4) — European number format -> LLM fallback path
    "V-STORMFACT": (
        5,
        "Hello,\n\nwe confirm the June creative production services, invoice "
        "SF-2026-0442, total GBP 72.000,00. The invoice follows in early July.\n\n"
        "Kind regards,\nEleanor Voss\nStormfactory Creative Ltd",
        [("SF-2026-0442-proforma.pdf", b"%PDF-1.4 placeholder proforma")],
    ),
    # Snowflake — reports a higher true-up -> variance breach at 5% -> held
    "V-SNOWFLAKE": (
        7,
        "Hi,\n\nThe June warehouse consumption true-up came to $212,400.00 "
        "(invoice SNOW-INV-77120, dated 2026-07-09), a little above the estimate. "
        "It's on its way to AP.\n\nAvery\nSnowflake",
        [],
    ),
    # Brooklyn Sports — prorated sponsorship, agrees within threshold
    "V-BSE": (
        7,
        "Hello,\n\nWe confirm $293,478.26 for the June share of the Barclays "
        "Center marketing sponsorship (PO-2108). Quarterly invoice issues "
        "2026-07-12.\n\nJamie\nBrooklyn Sports & Entertainment",
        [],
    ),
    # Contentsquare (GBP) — clean confirm with invoice + date
    "V-CONTENTSQ": (
        7,
        "Bonjour,\n\nWe confirm £48,000.00 for June 2026 experience-analytics "
        "services, invoice INV-CS-3391, dated 2026-07-14.\n\nMerci,\nLucie\n"
        "Contentsquare",
        [],
    ),
    # V-STRIPE never replies -> full reminder ladder + non-responsive escalation
    # V-RXR has no verified contact -> blocked send
    # V-APEXSTAFF has no GL mapping -> unmapped-vendor escalation
}
