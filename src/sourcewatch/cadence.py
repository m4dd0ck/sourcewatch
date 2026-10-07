"""Freshness against a source's own cadence: calendar days, business days, months or hours."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays

from sourcewatch.clock import local_date
from sourcewatch.config import CadenceConfig
from sourcewatch.observation import Observation
from sourcewatch.types import Status


@dataclass(frozen=True)
class FreshnessVerdict:
    """How stale a source is relative to what its cadence promises."""

    status: Status
    age: float
    unit: str
    ok_within: float
    fail_after: float
    reference: str
    message: str


def last_day_of_month(day: date) -> date:
    return day.replace(day=monthrange(day.year, day.month)[1])


def shift_months(day: date, months: int) -> date:
    """Move ``day`` by ``months`` whole months, clamping to the last valid day."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return day.replace(year=year, month=month, day=min(day.day, monthrange(year, month)[1]))


def us_holidays(*years: int) -> holidays.HolidayBase:
    return holidays.country_holidays("US", years=list(years))


def is_business_day(day: date, calendar: holidays.HolidayBase) -> bool:
    return day.weekday() < 5 and day not in calendar


def previous_business_day(day: date, calendar: holidays.HolidayBase) -> date:
    """The last business day strictly before ``day``."""
    cursor = day - timedelta(days=1)
    while not is_business_day(cursor, calendar):
        cursor -= timedelta(days=1)
    return cursor


def business_days_between(start: date, end: date, calendar: holidays.HolidayBase) -> int:
    """Business days in the half-open range (start, end]; zero when end is not after start."""
    if end <= start:
        return 0
    count = 0
    cursor = start + timedelta(days=1)
    while cursor <= end:
        if is_business_day(cursor, calendar):
            count += 1
        cursor += timedelta(days=1)
    return count


def _grade(age: float, ok_within: float, fail_after: float) -> Status:
    if age <= ok_within:
        return Status.OK
    if age <= fail_after:
        return Status.DEGRADED
    return Status.FAILED


def freshness(cfg: CadenceConfig, obs: Observation, now: datetime) -> FreshnessVerdict:
    """Grade an observation's freshness for the cadence it is expected to keep."""
    if not obs.available:
        return FreshnessVerdict(Status.UNKNOWN, 0, "", 0, 0, "", "not observed")

    if cfg.kind == "continuous":
        if obs.latest_event_at is None:
            return _no_timestamp(cfg, "hours")
        age_hours = max((now - obs.latest_event_at).total_seconds() / 3600, 0.0)
        ok, fail = cfg.ok_within.total_hours, cfg.fail_after.total_hours
        return FreshnessVerdict(
            _grade(age_hours, ok, fail),
            round(age_hours, 2),
            "hours",
            ok,
            fail,
            now.isoformat(),
            f"newest event {age_hours:.1f} h ago (ok within {ok:g} h)",
        )

    today = local_date(now, cfg.tz)
    ok_days, fail_days = cfg.ok_within.total_days, cfg.fail_after.total_days

    if cfg.kind == "monthly":
        if obs.latest_period is None:
            return _no_timestamp(cfg, "days")
        period_end = last_day_of_month(obs.latest_period)
        age_days = max((today - period_end).days, 0)
        return FreshnessVerdict(
            _grade(age_days, ok_days, fail_days),
            age_days,
            "days",
            ok_days,
            fail_days,
            period_end.isoformat(),
            f"newest month {obs.latest_period:%Y-%m}, {age_days} days after month end "
            f"(ok within {ok_days:g})",
        )

    if obs.latest_event_at is None:
        return _no_timestamp(cfg, "days")
    latest_local = obs.latest_event_at.astimezone(ZoneInfo(cfg.tz)).date()

    if cfg.kind == "daily":
        reference = today - timedelta(days=1)
        age_days = max((reference - latest_local).days, 0)
        return FreshnessVerdict(
            _grade(age_days, ok_days, fail_days),
            age_days,
            "days",
            ok_days,
            fail_days,
            reference.isoformat(),
            f"newest record {latest_local}, {age_days} days behind {reference} "
            f"(ok within {ok_days:g})",
        )

    calendar = us_holidays(today.year - 1, today.year, today.year + 1)
    reference = previous_business_day(today, calendar)
    age_bdays = business_days_between(latest_local, reference, calendar)
    return FreshnessVerdict(
        _grade(age_bdays, ok_days, fail_days),
        age_bdays,
        "business days",
        ok_days,
        fail_days,
        reference.isoformat(),
        f"newest record {latest_local}, {age_bdays} business days behind {reference} "
        f"(ok within {ok_days:g})",
    )


def _no_timestamp(cfg: CadenceConfig, unit: str) -> FreshnessVerdict:
    return FreshnessVerdict(
        Status.UNKNOWN, 0, unit, 0, 0, "", "source answered but reported no timestamp"
    )
