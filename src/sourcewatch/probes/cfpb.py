"""CFPB consumer complaint search API."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sourcewatch.config import CfpbProbe, SourceConfig
from sourcewatch.errors import ProbeError
from sourcewatch.observation import Observation
from sourcewatch.probes.base import ProbeContext, json_fields, write_sample


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(UTC)


def probe(source: SourceConfig, ctx: ProbeContext) -> Observation:
    cfg = source.probe
    assert isinstance(cfg, CfpbProbe)
    observed_at = ctx.now()

    latest = ctx.get_json(
        cfg.endpoint,
        params={"size": cfg.sample_size, "sort": "created_date_desc", "no_aggs": "true"},
    )
    hits: list[dict[str, Any]] = (latest.get("hits") or {}).get("hits") or []
    rows = [dict(h.get("_source") or {}) for h in hits]
    if not rows:
        raise ProbeError("CFPB search returned no complaints")
    newest = max(_parse(r["date_received"]) for r in rows if r.get("date_received"))

    # Reason: the newest complete day the API has caught up on is two UTC days back; counting
    # yesterday would always look like a collapse.
    day = (observed_at.date() - timedelta(days=cfg.count_lag_days)).isoformat()
    counted = ctx.get_json(
        cfg.endpoint,
        params={
            "size": 0,
            "no_aggs": "true",
            "date_received_min": day,
            "date_received_max": day,
        },
    )
    total = ((counted.get("hits") or {}).get("total") or {}).get("value")
    count = float(total) if total is not None else None

    sample = write_sample(rows, ctx.workdir / f"{source.id}.parquet")
    return Observation(
        source_id=source.id,
        observed_at=observed_at,
        available=True,
        http=ctx.last_fetched.detail if ctx.last_fetched else None,
        latest_event_at=newest,
        count=count,
        count_label=source.volume.metric,
        fields=json_fields(rows),
        sample_parquet=sample,
        notes=f"latest {newest:%Y-%m-%d}, {count:,.0f} received on {day}"
        if count is not None
        else f"latest {newest:%Y-%m-%d}, count unavailable",
    )
