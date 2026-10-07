from datetime import UTC, date, datetime

import pytest

from sourcewatch.cadence import (
    business_days_between,
    freshness,
    previous_business_day,
    shift_months,
    us_holidays,
)
from sourcewatch.config import CadenceConfig, Duration
from sourcewatch.observation import Observation
from sourcewatch.types import Status


def obs(**kwargs: object) -> Observation:
    base: dict[str, object] = {
        "source_id": "x",
        "observed_at": datetime(2026, 10, 7, 11, tzinfo=UTC),
        "available": True,
    }
    base.update(kwargs)
    return Observation.model_validate(base)


def cadence(kind: str, tz: str, ok: Duration, fail: Duration) -> CadenceConfig:
    return CadenceConfig(kind=kind, tz=tz, ok_within=ok, fail_after=fail)  # type: ignore[arg-type]


def test_shift_months_clamps_to_month_end():
    assert shift_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert shift_months(date(2026, 1, 15), -2) == date(2025, 11, 15)
    assert shift_months(date(2025, 12, 1), 1) == date(2026, 1, 1)


def test_business_day_helpers_skip_weekends_and_holidays():
    cal = us_holidays(2026)
    # Monday 2026-10-12 is Columbus Day; Friday 10-09 is the previous business day of Tuesday 10-13.
    assert previous_business_day(date(2026, 10, 13), cal) == date(2026, 10, 9)
    assert previous_business_day(date(2026, 10, 12), cal) == date(2026, 10, 9)
    assert business_days_between(date(2026, 10, 9), date(2026, 10, 13), cal) == 1
    assert business_days_between(date(2026, 10, 13), date(2026, 10, 9), cal) == 0


@pytest.mark.parametrize(
    ("now", "latest", "expected"),
    [
        # Wednesday run: newest record Monday → previous business day is Tuesday → 1 bday behind.
        (datetime(2026, 10, 7, 11, tzinfo=UTC), datetime(2026, 10, 5, 12, tzinfo=UTC), Status.OK),
        # Saturday run: newest Thursday → reference Friday → 1 behind, still ok.
        (datetime(2026, 10, 10, 11, tzinfo=UTC), datetime(2026, 10, 8, 12, tzinfo=UTC), Status.OK),
        # Tuesday after Columbus Day: newest Wed 10-07, reference Fri 10-09, 2 behind, still ok.
        (datetime(2026, 10, 13, 11, tzinfo=UTC), datetime(2026, 10, 7, 12, tzinfo=UTC), Status.OK),
        # Newest a week old → 4 business days behind → degraded (fail_after 4).
        (
            datetime(2026, 10, 7, 11, tzinfo=UTC),
            datetime(2026, 9, 30, 12, tzinfo=UTC),
            Status.DEGRADED,
        ),
        # Two weeks old → failed.
        (
            datetime(2026, 10, 7, 11, tzinfo=UTC),
            datetime(2026, 9, 22, 12, tzinfo=UTC),
            Status.FAILED,
        ),
    ],
)
def test_business_days_freshness(now: datetime, latest: datetime, expected: Status):
    cfg = cadence("business_days", "America/New_York", Duration(days=2), Duration(days=4))
    verdict = freshness(cfg, obs(latest_event_at=latest), now)
    assert verdict.status == expected, verdict.message
    assert verdict.unit == "business days"


@pytest.mark.parametrize(
    ("latest_day", "expected"),
    [
        (date(2026, 10, 6), Status.OK),
        (date(2026, 10, 4), Status.OK),
        (date(2026, 10, 3), Status.DEGRADED),
        (date(2026, 9, 30), Status.FAILED),
    ],
)
def test_daily_freshness_measures_against_yesterday_local(latest_day: date, expected: Status):
    cfg = cadence("daily", "America/New_York", Duration(days=2), Duration(days=4))
    now = datetime(2026, 10, 7, 11, tzinfo=UTC)  # 07:00 ET on the 7th → reference is the 6th
    latest = datetime(latest_day.year, latest_day.month, latest_day.day, 6, tzinfo=UTC)  # 02:00 ET
    verdict = freshness(cfg, obs(latest_event_at=latest), now)
    assert verdict.status == expected, verdict.message
    assert verdict.reference == "2026-10-06"


@pytest.mark.parametrize(
    ("period", "expected", "age"),
    [
        (date(2026, 8, 1), Status.OK, 37),  # observed 2026-08 on 10-07: 37 days after Aug 31
        (date(2026, 7, 1), Status.DEGRADED, 68),  # July missing August: 68 days
        (date(2026, 6, 1), Status.FAILED, 99),
    ],
)
def test_monthly_freshness_counts_days_after_month_end(period: date, expected: Status, age: int):
    cfg = cadence("monthly", "America/New_York", Duration(days=45), Duration(days=75))
    verdict = freshness(cfg, obs(latest_period=period), datetime(2026, 10, 7, 11, tzinfo=UTC))
    assert (verdict.status, verdict.age) == (expected, age), verdict.message


def test_continuous_freshness_in_hours():
    cfg = cadence("continuous", "UTC", Duration(hours=6), Duration(hours=24))
    now = datetime(2026, 10, 7, 11, tzinfo=UTC)
    assert freshness(cfg, obs(latest_event_at=now.replace(hour=10)), now).status == Status.OK
    assert freshness(cfg, obs(latest_event_at=now.replace(hour=1)), now).status == Status.DEGRADED
    old = obs(latest_event_at=datetime(2026, 10, 5, tzinfo=UTC))
    assert freshness(cfg, old, now).status == Status.FAILED


def test_unavailable_or_timestampless_is_unknown():
    cfg = cadence("daily", "UTC", Duration(days=1), Duration(days=3))
    now = datetime(2026, 10, 7, 11, tzinfo=UTC)
    assert freshness(cfg, obs(available=False), now).status == Status.UNKNOWN
    assert freshness(cfg, obs(), now).status == Status.UNKNOWN
