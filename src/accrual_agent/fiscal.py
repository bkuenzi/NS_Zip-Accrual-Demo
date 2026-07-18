"""Fiscal-calendar module.

All period resolution and close-day derivation lives here: calendar-month
periods by default, explicit custom periods (4-4-5 etc.) from
config/close_calendar.yaml when ``calendar_type: custom``.

Close-day convention: day 1 of close = the first (business) day after the
period ends. Reminder cadence, checkpoints, and close-risk flags all key off
the close day, computed in the configured close timezone.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .config import CONFIG_DIR


@dataclass(frozen=True)
class Period:
    name: str
    start: dt.date
    end: dt.date

    def contains(self, day: dt.date) -> bool:
        return self.start <= day <= self.end


class FiscalCalendar:
    def __init__(
        self,
        calendar_type: str = "calendar_month",
        periods: list[Period] | None = None,
        business_days: bool = True,
        final_close_day: int = 10,
        timezone: str = "America/New_York",
    ) -> None:
        self.calendar_type = calendar_type
        self.custom_periods = sorted(periods or [], key=lambda p: p.start)
        self.business_days = business_days
        self.final_close_day = final_close_day
        self.tz = ZoneInfo(timezone)
        if calendar_type == "custom" and not self.custom_periods:
            raise ValueError("calendar_type: custom requires an explicit periods list")

    @classmethod
    def load(cls, path: Path | None = None, timezone: str = "America/New_York") -> FiscalCalendar:
        path = path or CONFIG_DIR / "close_calendar.yaml"
        raw = yaml.safe_load(path.read_text()) or {}
        periods = [
            Period(name=str(p["name"]), start=_as_date(p["start"]), end=_as_date(p["end"]))
            for p in (raw.get("periods") or [])
        ]
        return cls(
            calendar_type=str(raw.get("calendar_type", "calendar_month")),
            periods=periods,
            business_days=bool(raw.get("business_days", True)),
            final_close_day=int(raw.get("final_close_day", 10)),
            timezone=timezone,
        )

    # ── period resolution ────────────────────────────────────────────────

    def period_for(self, day: dt.date) -> Period:
        if self.calendar_type == "custom":
            for p in self.custom_periods:
                if p.contains(day):
                    return p
            raise ValueError(f"{day} falls outside every configured custom period")
        start = day.replace(day=1)
        end = _month_end(start)
        return Period(name=f"{day.year:04d}-{day.month:02d}", start=start, end=end)

    def period_by_name(self, name: str) -> Period:
        if self.calendar_type == "custom":
            for p in self.custom_periods:
                if p.name == name:
                    return p
            raise ValueError(f"unknown custom period {name!r}")
        year, month = (int(x) for x in name.split("-"))
        start = dt.date(year, month, 1)
        return Period(name=name, start=start, end=_month_end(start))

    def prior_period(self, period: Period, back: int = 1) -> Period:
        p = period
        for _ in range(back):
            p = self.period_for(p.start - dt.timedelta(days=1))
        return p

    def next_period(self, period: Period) -> Period:
        return self.period_for(period.end + dt.timedelta(days=1))

    # ── close-day math ───────────────────────────────────────────────────

    def closing_period(self, today: dt.date) -> Period:
        """The period currently being closed: the one that most recently ended."""
        current = self.period_for(today)
        return self.prior_period(current)

    def close_day(self, today: dt.date, period: Period | None = None) -> int:
        """1-based close day for `today` relative to `period`'s end.

        0 = the period has not ended yet; capped at final_close_day + horizon
        so late runs still register as "past final day".
        """
        period = period or self.closing_period(today)
        if today <= period.end:
            return 0
        if self.business_days:
            day = 0
            cursor = period.end
            while cursor < today:
                cursor += dt.timedelta(days=1)
                if cursor.weekday() < 5:
                    day += 1
            return day
        return (today - period.end).days

    def today(self) -> dt.date:
        return dt.datetime.now(self.tz).date()

    def add_business_days(self, day: dt.date, count: int) -> dt.date:
        step = 1 if count >= 0 else -1
        remaining = abs(count)
        cursor = day
        while remaining:
            cursor += dt.timedelta(days=step)
            if cursor.weekday() < 5:
                remaining -= 1
        return cursor


def _month_end(start: dt.date) -> dt.date:
    if start.month == 12:
        return dt.date(start.year, 12, 31)
    return dt.date(start.year, start.month + 1, 1) - dt.timedelta(days=1)


def _as_date(value: object) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))
