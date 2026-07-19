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
            # Below the materiality floor individually — but together these
            # sub-floor gaps cross the aggregate threshold and raise one
            # "sundry accruals" line for controller review.
            *[
                ZipRequisition(
                    requisition_id=rid,
                    vendor_id=vid,
                    vendor_name=name,
                    business_unit="BU-US",
                    committed_amount=D(amount),
                    approved_date=dt.date(2026, 6, 12),
                    service_start=dt.date(2026, 6, 1),
                    service_end=dt.date(2026, 6, 30),
                    gl_account="6800",
                    cost_center="CC-OPS",
                )
                for rid, vid, name, amount in [
                    ("ZR-2101", "V-IOTA", "Iota Snacks", "180.00"),
                    ("ZR-2102", "V-KAPPA", "Kappa Office Plants", "210.00"),
                    ("ZR-2103", "V-LAMBDA", "Lambda Water Coolers", "145.00"),
                    ("ZR-2104", "V-MU", "Mu Courier Service", "95.00"),
                    ("ZR-2105", "V-NU", "Nu Stock Photos", "230.00"),
                    ("ZR-2106", "V-XI", "Xi Domain Renewals", "160.00"),
                ]
            ],
        ]

    def get_approved_requisitions(
        self, start: dt.date, end: dt.date
    ) -> list[ZipRequisition]:
        return [
            r for r in self.requisitions
            if (r.service_end or r.approved_date) >= start and r.approved_date <= end
        ]
