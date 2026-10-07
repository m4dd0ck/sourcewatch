"""Open-Meteo historical weather archive for one location."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sourcewatch.clock import local_date
from sourcewatch.config import OpenMeteoProbe, SourceConfig
from sourcewatch.errors import ProbeError
from sourcewatch.observation import Observation
from sourcewatch.probes.base import ProbeContext, json_fields, write_sample


def probe(source: SourceConfig, ctx: ProbeContext) -> Observation:
    cfg = source.probe
    assert isinstance(cfg, OpenMeteoProbe)
    observed_at = ctx.now()
    today = local_date(observed_at, cfg.tz)
    params = {
        "latitude": cfg.latitude,
        "longitude": cfg.longitude,
        "start_date": (today - timedelta(days=cfg.days)).isoformat(),
        "end_date": today.isoformat(),
        "daily": ",".join(cfg.daily),
        "hourly": ",".join(cfg.hourly),
        "timezone": cfg.tz,
    }
    data = ctx.get_json(cfg.endpoint, params=params)
    if "error" in data:
        raise ProbeError(f"Open-Meteo rejected the request: {data.get('reason')}")

    daily = data.get("daily") or {}
    days: list[str] = daily.get("time") or []
    lead = cfg.daily[0]
    present = [d for d, v in zip(days, daily.get(lead) or [], strict=False) if v is not None]
    latest_day = date.fromisoformat(present[-1]) if present else None

    hourly = data.get("hourly") or {}
    yesterday = (today - timedelta(days=1)).isoformat()
    hourly_rows = sum(
        1
        for t, v in zip(hourly.get("time") or [], hourly.get(cfg.hourly[0]) or [], strict=False)
        if t.startswith(yesterday) and v is not None
    )

    rows: list[dict[str, Any]] = [
        {"time": d, **{var: (daily.get(var) or [None] * len(days))[i] for var in cfg.daily}}
        for i, d in enumerate(days)
    ]
    sample = write_sample(rows, ctx.workdir / f"{source.id}.parquet") if rows else None
    tz = ZoneInfo(cfg.tz)
    return Observation(
        source_id=source.id,
        observed_at=observed_at,
        available=True,
        http=ctx.last_fetched.detail if ctx.last_fetched else None,
        latest_event_at=(
            datetime.combine(latest_day, datetime.min.time(), tz).astimezone(UTC)
            if latest_day
            else None
        ),
        count=float(hourly_rows),
        count_label=source.volume.metric,
        fields=json_fields(rows) if rows else None,
        sample_parquet=sample,
        notes=(
            f"archive through {latest_day}, {hourly_rows} hourly rows on {yesterday}"
            if latest_day
            else "archive returned no daily values"
        ),
    )
