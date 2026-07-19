"""Outbound vendor communications with the verified-contact hard gate.

A line only ever receives the next unsent due stage per cycle (initial, then
escalating reminders). No verified contact on file — or a contact whose domain
fails the vendor-master cross-check — blocks the send entirely, flags the
line, and raises a MISSING_CONTACT escalation for human input.
"""

from __future__ import annotations

import datetime as dt

from ..config import Settings, VendorContactStore
from ..logging_setup import get_logger
from ..models import (
    AccrualLine,
    CommRecord,
    EscalationReason,
    ThreadStatus,
)
from ..register.repository import Repository
from ..register.service import RegisterService
from .cadence import Cadence
from .mailer import MockMailer, OutboundEmail
from .templates import TemplateEngine

log = get_logger(__name__)


class OutboundService:
    def __init__(
        self,
        settings: Settings,
        repo: Repository,
        register: RegisterService,
        mailer,                                     # SmtpImapMailer | MockMailer
        templates: TemplateEngine,
        cadence: Cadence,
        contacts: VendorContactStore,
        vendor_domains: dict[str, list[str]],       # from the NetSuite vendor master
    ) -> None:
        self.settings = settings
        self.repo = repo
        self.register = register
        self.mailer = mailer
        self.templates = templates
        self.cadence = cadence
        self.contacts = contacts
        self.vendor_domains = vendor_domains

    def process(self, close_day: int) -> tuple[int, list[EscalationReason]]:
        """Send due outreach for every line still awaiting confirmation.

        Returns (emails_sent, escalation_reasons_raised_as_flags). The actual
        escalation records are raised by the escalation engine from the flags
        set here.
        """
        sent_count = 0
        flags: list[EscalationReason] = []
        candidates = [
            line
            for line in self.register.unconfirmed_lines(period=None) or []
            if not line.comm_suppressed
        ]
        for line in candidates:
            if line.thread_status == ThreadStatus.REPLIED:
                continue
            if self._send_next_stage(line, close_day):
                sent_count += 1
            refreshed = self.repo.get_line(line.line_id)
            if refreshed and refreshed.thread_status == ThreadStatus.BLOCKED_NO_CONTACT:
                flags.append(EscalationReason.MISSING_CONTACT)
        return sent_count, flags

    def _send_next_stage(self, line: AccrualLine, close_day: int) -> bool:
        sent_stages = self.repo.sent_stages(line.line_id)
        stage = self.cadence.next_unsent_stage(close_day, sent_stages)
        if stage is None:
            if self.cadence.ladder_exhausted(close_day, sent_stages) and (
                line.thread_status == ThreadStatus.AWAITING_REPLY
            ):
                self.register.update_fields(
                    line.line_id, source="outbound",
                    thread_status=ThreadStatus.EXHAUSTED,
                )
            return False

        contact, block_reason = self.contacts.verified_contact(
            line.vendor_id, self.vendor_domains.get(line.vendor_id, [])
        )
        if contact is None:
            if line.thread_status != ThreadStatus.BLOCKED_NO_CONTACT:
                log.warning(
                    "outbound.blocked_no_contact",
                    line_id=line.line_id, vendor=line.vendor_id, reason=block_reason,
                )
                self.register.update_fields(
                    line.line_id, source="outbound",
                    thread_status=ThreadStatus.BLOCKED_NO_CONTACT,
                    notes=f"outbound blocked: {block_reason}",
                )
            return False

        subject, body = self.templates.render_stage_email(
            stage,
            vendor_name=line.vendor_name,
            contact_name=contact.name or "there",
            period=line.period,
            ref_token=line.ref_token,
            source_ref=line.source_ref,
            amount=f"{line.amount:,.2f}",
            currency=line.currency,
            company_name=self.settings.effective_company_name,
            mailbox_address=self.settings.mailbox_address,
            initial_sent_date=(self.repo.initial_sent_at(line.line_id) or "")[:10],
            prior_attempts=len(sent_stages),
        )
        message = OutboundEmail(to=contact.email, subject=subject, body=body)
        if isinstance(self.mailer, MockMailer):
            self.mailer.register_outbound_vendor(message.message_id, line.vendor_id)
        delivery = self.mailer.send(message)

        self.repo.add_comm(CommRecord(
            line_id=line.line_id,
            direction="outbound",
            stage=stage.value,
            recipient=contact.email,
            sender=self.settings.mailbox_address,
            subject=subject,
            message_id=message.message_id,
            body_preview=body[:400],
            sent_at=dt.datetime.now(dt.UTC),
            delivery=delivery,
        ))
        self.register.update_fields(
            line.line_id, source="outbound", thread_status=ThreadStatus.AWAITING_REPLY
        )
        log.info(
            "outbound.sent",
            line_id=line.line_id, vendor=line.vendor_id, stage=stage.value,
            urgency=self.cadence.urgency(stage), delivery=delivery,
        )
        return True

    def flag_close_risk(self, close_day: int) -> list[AccrualLine]:
        """Mark unconfirmed lines as close-risk once the deadline approaches."""
        if not self.cadence.is_close_risk(close_day):
            return []
        flagged = []
        for line in self.register.unconfirmed_lines(period=None):
            if not line.close_risk:
                self.register.update_fields(
                    line.line_id, source="cadence", close_risk=True
                )
                flagged.append(line)
        return flagged
