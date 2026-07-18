from __future__ import annotations

import datetime as dt

import pytest

from accrual_agent.config import Settings
from accrual_agent.runtime import Runtime


@pytest.fixture
def settings(tmp_path) -> Settings:
    base = Settings(_env_file=None)
    return base.model_copy(update={
        "mode": "mock",
        "outbound_mode": "dry_run",
        "db_path": str(tmp_path / "test.db"),
        "output_dir": str(tmp_path / "output"),
        "artifacts_dir": str(tmp_path / "artifacts"),
        "team_lead_email": "lead@yourco.example",
        "escalation_channels": "email",
    })


def simulated_now(day: int, settings: Settings) -> dt.datetime:
    """9am company-time on the Nth business day after the 2026-06 period end."""
    from zoneinfo import ZoneInfo

    probe = Runtime(settings)
    run_date = probe.calendar.add_business_days(dt.date(2026, 6, 30), day)
    return dt.datetime.combine(
        run_date, dt.time(9, 0), tzinfo=ZoneInfo(settings.close_timezone)
    )


@pytest.fixture
def runtime_factory(settings):
    def build(day: int = 1) -> Runtime:
        return Runtime(settings, now_provider=lambda: simulated_now(day, settings))

    return build
