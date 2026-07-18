"""API-sourced accruals: ad-platform actuals netted against posted invoices.

When a vendor's spend is available from its own API, the accrual is
auto-confirmed from data and outbound vendor email is suppressed. Ad platforms
restate spend for up to the settle window after period end, so lines pulled
inside that window stay `provisional`: re-pulled and adjusted every cycle,
posted only once settled (or forced on the final close day with the latest
number).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from ..config import GLMappingStore, Settings
from ..fiscal import Period
from ..integrations.base import DataAnomalyError, IntegrationError
from ..integrations.factory import AdapterSet
from ..logging_setup import get_logger
from ..models import AccrualStatus, EscalationReason
from ..register.service import RegisterService

log = get_logger(__name__)

TWO_PLACES = Decimal("0.01")


class ApiAccrualService:
    def __init__(
        self,
        settings: Settings,
        adapters: AdapterSet,
        register: RegisterService,
        gl_store: GLMappingStore,
    ) -> None:
        self.settings = settings
        self.adapters = adapters
        self.register = register
        self.gl_store = gl_store

    def run(
        self, period: Period, now: dt.datetime
    ) -> tuple[int, list[tuple[EscalationReason, str]]]:
        """Pull actuals from every connected platform. Returns
        (lines_touched, escalation_flags)."""
        touched = 0
        flags: list[tuple[EscalationReason, str]] = []
        bills = self.adapters.netsuite.get_vendor_bills(period.start, period.end)
        settled_at = dt.datetime.combine(
            period.end, dt.time(23, 59, 59), tzinfo=dt.UTC
        ) + dt.timedelta(hours=self.settings.ad_settle_hours)

        for platform in self.adapters.ad_platforms:
            try:
                records = platform.get_spend(period.start, period.end)
            except DataAnomalyError as exc:
                flags.append((EscalationReason.API_ANOMALY, str(exc)))
                continue
            except IntegrationError as exc:
                flags.append((EscalationReason.API_FAILURE, str(exc)))
                continue

            for record in records:
                mapping = self.gl_store.ad_accounts.get(record.account_id)
                if mapping is None:
                    flags.append((
                        EscalationReason.API_ANOMALY,
                        f"{record.platform.value} account {record.account_id} has no "
                        "vendor/subsidiary mapping in gl_mappings.yaml",
                    ))
                    continue
                vendor_id = mapping["vendor_id"]
                billed = sum(
                    (b.amount for b in bills if b.vendor_id == vendor_id),
                    Decimal("0"),
                )
                net = (record.spend - billed).quantize(TWO_PLACES)
                if net <= 0:
                    log.info(
                        "api_accruals.fully_billed", vendor=vendor_id,
                        spend=str(record.spend), billed=str(billed),
                    )
                    continue
                provisional = record.as_of < settled_at
                if self._apply(period, record.platform, record.account_id, vendor_id,
                               mapping.get("subsidiary_id"), net, record.spend,
                               billed, record.currency, provisional):
                    touched += 1
        return touched, flags

    def _apply(
        self,
        period: Period,
        platform,
        account_id: str,
        vendor_id: str,
        subsidiary_id: str | None,
        net: Decimal,
        spend: Decimal,
        billed: Decimal,
        currency: str,
        provisional: bool,
    ) -> bool:
        coding = self.gl_store.coding_for(vendor_id)
        fx = self.adapters.netsuite.get_exchange_rate(currency, period.end)
        basis = (
            f"{platform.value} actuals {spend:,.2f} less {billed:,.2f} billed in "
            f"NetSuite ({'provisional — settle window open' if provisional else 'settled'})"
        )
        line, created = self.register.upsert_line(
            vendor_id=vendor_id,
            vendor_name=vendor_id,
            period=period.name,
            source_type=platform,
            source_ref=account_id,
            estimate_basis=basis,
            amount=net,
            currency=currency,
            exchange_rate=fx,
            gl_account=coding.gl_account if coding else None,
            cost_center=coding.cost_center if coding else None,
            subsidiary_id=subsidiary_id,
            provisional=provisional,
            comm_suppressed=True,               # API data replaces vendor outreach
            source="api_accruals",
        )
        if created and line.vendor_name == vendor_id:
            vendors = {v.vendor_id: v.name for v in self.adapters.netsuite.get_vendors()}
            if vendor_id in vendors:
                line = self.register.update_fields(
                    line.line_id, source="api_accruals", vendor_name=vendors[vendor_id]
                )

        if line.status == AccrualStatus.ESTIMATED:
            self.register.transition(
                line, AccrualStatus.AUTO_CONFIRMED, source="api_accruals",
                confirmed_amount=net,
                confirmed_source=platform.value,
                provisional=provisional,
            )
            return True
        if line.status == AccrualStatus.AUTO_CONFIRMED and (
            line.confirmed_amount != net or line.provisional != provisional
        ):
            # lag-aware refresh: restated figure adjusts the open accrual
            log.info(
                "api_accruals.restated", line_id=line.line_id,
                old=str(line.confirmed_amount), new=str(net),
                provisional=provisional,
            )
            self.register.transition(
                line, AccrualStatus.AUTO_CONFIRMED, source="api_accruals",
                confirmed_amount=net, amount=net,
                base_amount=(net * fx).quantize(TWO_PLACES),
                estimate_basis=basis, provisional=provisional,
            )
            return True
        return False
