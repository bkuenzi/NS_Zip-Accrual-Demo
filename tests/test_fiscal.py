import datetime as dt

import pytest

from accrual_agent.fiscal import FiscalCalendar, Period


def make_calendar(**kwargs) -> FiscalCalendar:
    defaults = {"business_days": True, "final_close_day": 10, "timezone": "America/New_York"}
    defaults.update(kwargs)
    return FiscalCalendar(**defaults)


def test_calendar_month_period_resolution():
    cal = make_calendar()
    period = cal.period_for(dt.date(2026, 6, 15))
    assert period.name == "2026-06"
    assert period.start == dt.date(2026, 6, 1)
    assert period.end == dt.date(2026, 6, 30)


def test_closing_period_is_prior_month():
    cal = make_calendar()
    assert cal.closing_period(dt.date(2026, 7, 8)).name == "2026-06"


def test_close_day_skips_weekends():
    cal = make_calendar()
    june = cal.period_by_name("2026-06")
    # 2026-06-30 is a Tuesday; Jul 1 = day 1 ... Jul 3 (Fri) = day 3,
    # Jul 6 (Mon) = day 4
    assert cal.close_day(dt.date(2026, 7, 1), june) == 1
    assert cal.close_day(dt.date(2026, 7, 3), june) == 3
    assert cal.close_day(dt.date(2026, 7, 6), june) == 4
    assert cal.close_day(dt.date(2026, 6, 20), june) == 0


def test_prior_and_next_period():
    cal = make_calendar()
    june = cal.period_by_name("2026-06")
    assert cal.prior_period(june).name == "2026-05"
    assert cal.prior_period(june, back=2).name == "2026-04"
    assert cal.next_period(june).name == "2026-07"
    assert cal.next_period(june).start == dt.date(2026, 7, 1)


def test_custom_445_calendar():
    periods = [
        Period("2026-P07", dt.date(2026, 6, 29), dt.date(2026, 7, 26)),
        Period("2026-P08", dt.date(2026, 7, 27), dt.date(2026, 8, 23)),
    ]
    cal = make_calendar(calendar_type="custom", periods=periods)
    assert cal.period_for(dt.date(2026, 7, 10)).name == "2026-P07"
    assert cal.period_by_name("2026-P08").end == dt.date(2026, 8, 23)
    assert cal.prior_period(cal.period_by_name("2026-P08")).name == "2026-P07"
    with pytest.raises(ValueError):
        cal.period_for(dt.date(2026, 1, 1))


def test_custom_calendar_requires_periods():
    with pytest.raises(ValueError):
        make_calendar(calendar_type="custom")


def test_add_business_days():
    cal = make_calendar()
    # Tue Jun 30 + 3 business days = Fri Jul 3; + 4 = Mon Jul 6
    assert cal.add_business_days(dt.date(2026, 6, 30), 3) == dt.date(2026, 7, 3)
    assert cal.add_business_days(dt.date(2026, 6, 30), 4) == dt.date(2026, 7, 6)
