"""Composition root: wires settings, storage, adapters, and services.

Every CLI command builds a Runtime; the demo passes a simulated clock so the
whole stack (ad-platform settle windows, cadence, escalation staleness) moves
through close days deterministically.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from functools import cached_property

from .comms.cadence import Cadence
from .comms.inbound import InboundService
from .comms.llm_extractor import build_extractor
from .comms.mailer import (
    DEMO_REPLY_FIXTURES,
    MVP_REPLY_FIXTURES,
    MockMailer,
    SmtpImapMailer,
)
from .comms.outbound import OutboundService
from .comms.templates import TemplateEngine
from .config import GLMappingStore, Settings, VendorContactStore
from .engine.api_accruals import ApiAccrualService
from .engine.confirmation import ConfirmationService
from .engine.escalation import EscalationService
from .engine.identification import IdentificationService
from .engine.writeback import WritebackService
from .fiscal import FiscalCalendar
from .integrations.factory import AdapterSet, build_adapters
from .register.repository import Repository
from .register.service import RegisterService


class Runtime:
    def __init__(
        self,
        settings: Settings | None = None,
        now_provider: Callable[[], dt.datetime] | None = None,
    ) -> None:
        from .config import get_settings

        self.settings = settings or get_settings()
        self.now_provider = now_provider or (lambda: dt.datetime.now(dt.UTC))

    def now(self) -> dt.datetime:
        return self.now_provider()

    @cached_property
    def repo(self) -> Repository:
        return Repository(self.settings.db_path)

    @cached_property
    def register(self) -> RegisterService:
        return RegisterService(self.repo)

    @cached_property
    def calendar(self) -> FiscalCalendar:
        return FiscalCalendar.load(timezone=self.settings.close_timezone)

    @cached_property
    def gl_store(self) -> GLMappingStore:
        return GLMappingStore.load(self.settings.config_dir / "gl_mappings.yaml")

    @cached_property
    def contacts(self) -> VendorContactStore:
        return VendorContactStore.load(self.settings.config_dir / "vendor_contacts.yaml")

    @cached_property
    def templates(self) -> TemplateEngine:
        return TemplateEngine()

    @cached_property
    def cadence(self) -> Cadence:
        return Cadence(self.settings.reminder_days, self.calendar.final_close_day)

    @cached_property
    def adapters(self) -> AdapterSet:
        return build_adapters(self.settings, now_provider=self.now_provider)

    @cached_property
    def mailer(self):
        if self.settings.mode == "mock":
            mailer = MockMailer(self.settings)
            fixtures = (
                MVP_REPLY_FIXTURES if self.settings.profile == "mvp"
                else DEMO_REPLY_FIXTURES
            )
            mailer.reply_fixtures = dict(fixtures)
            mailer.hydrate(self._prior_sends())
            return mailer
        return SmtpImapMailer(self.settings)

    def _prior_sends(self) -> list[tuple[str, str, str, str]]:
        sends = []
        for line in self.repo.lines():
            for comm in self.repo.comms_for_line(line.line_id):
                if comm.direction == "outbound" and comm.message_id and comm.recipient:
                    sends.append(
                        (line.vendor_id, comm.message_id, comm.recipient, comm.subject)
                    )
        return sends

    @cached_property
    def vendor_domains(self) -> dict[str, list[str]]:
        return {v.vendor_id: v.email_domains for v in self.adapters.netsuite.get_vendors()}

    # ── services ─────────────────────────────────────────────────────────

    @cached_property
    def identification(self) -> IdentificationService:
        return IdentificationService(self.settings, self.adapters, self.register, self.gl_store)

    @cached_property
    def api_accruals(self) -> ApiAccrualService:
        return ApiAccrualService(self.settings, self.adapters, self.register, self.gl_store)

    @cached_property
    def confirmation(self) -> ConfirmationService:
        return ConfirmationService(self.settings, self.register, self.gl_store)

    @cached_property
    def outbound(self) -> OutboundService:
        return OutboundService(
            self.settings, self.repo, self.register, self.mailer, self.templates,
            self.cadence, self.contacts, self.vendor_domains,
        )

    @cached_property
    def inbound(self) -> InboundService:
        return InboundService(
            self.repo, self.mailer, build_extractor(self.settings),
            self.settings.artifacts_dir,
        )

    @cached_property
    def writeback(self) -> WritebackService:
        return WritebackService(
            self.settings, self.adapters, self.register, self.gl_store,
            self.calendar, self.confirmation,
        )

    @cached_property
    def escalation(self) -> EscalationService:
        return EscalationService(
            self.settings, self.repo, self.register, self.mailer,
            self.templates, self.calendar,
        )
