"""Seeded Zip mock (read-only, like the real adapter)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from ...models import ZipRequisition

D = Decimal


class MockZip:
    def __init__(self) -> None:
        self.requisitions = [
            # Non-PO engagement: committed spend with no NetSuite bill -> accrual
            ZipRequisition(
                requisition_id="ZR-2088",
                vendor_id="V-GAMMA",
                vendor_name="Gamma Consulting",
                business_unit="BU-US",
                committed_amount=D("15000.00"),
                approved_date=dt.date(2026, 6, 5),
                service_start=dt.date(2026, 6, 1),
                service_end=dt.date(2026, 6, 30),
                gl_account="6620",
                cost_center="CC-FIN",
            ),
            # Became PO-1001 in NetSuite: identification must skip it (PO path owns it)
            ZipRequisition(
                requisition_id="ZR-2050",
                vendor_id="V-ACME",
                vendor_name="Acme Cloud Services",
                business_unit="BU-US",
                committed_amount=D("42000.00"),
                approved_date=dt.date(2026, 5, 28),
                service_start=dt.date(2026, 6, 1),
                service_end=dt.date(2026, 6, 30),
                po_number="PO-1001",
            ),
            # Below the materiality floor: logged, never accrued or emailed
            ZipRequisition(
                requisition_id="ZR-2101",
                vendor_id="V-IOTA",
                vendor_name="Iota Snacks",
                business_unit="BU-US",
                committed_amount=D("180.00"),
                approved_date=dt.date(2026, 6, 12),
                service_start=dt.date(2026, 6, 1),
                service_end=dt.date(2026, 6, 30),
                gl_account="6800",
                cost_center="CC-OPS",
            ),
        ]

    def get_approved_requisitions(
        self, start: dt.date, end: dt.date
    ) -> list[ZipRequisition]:
        return [
            r for r in self.requisitions
            if (r.service_end or r.approved_date) >= start and r.approved_date <= end
        ]
