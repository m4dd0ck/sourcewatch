"""USGS earthquake GeoJSON feed: regenerated every minute, a rolling 24-hour window."""

from datetime import UTC, datetime
from typing import Any

from sourcewatch.config import SourceConfig, UsgsProbe
from sourcewatch.observation import Observation
from sourcewatch.probes.base import ProbeContext, json_fields, write_sample

SAMPLE_ROWS = 200


def _ms(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def probe(source: SourceConfig, ctx: ProbeContext) -> Observation:
    cfg = source.probe
    assert isinstance(cfg, UsgsProbe)
    observed_at = ctx.now()
    data = ctx.get_json(cfg.feed_url)
    features = data.get("features") or []
    metadata = data.get("metadata") or {}

    rows: list[dict[str, Any]] = []
    newest: datetime | None = None
    for feature in features[:SAMPLE_ROWS]:
        props = dict(feature.get("properties") or {})
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None, None]
        props["longitude"], props["latitude"], props["depth_km"] = coords[:3]
        rows.append(props)
    for feature in features:
        when = (feature.get("properties") or {}).get("time")
        if when is not None:
            stamp = _ms(when)
            newest = stamp if newest is None or stamp > newest else newest

    sample = write_sample(rows, ctx.workdir / f"{source.id}.parquet") if rows else None
    count = float(metadata.get("count", len(features)))
    age_min = max((observed_at - newest).total_seconds() / 60, 0.0) if newest else None
    return Observation(
        source_id=source.id,
        observed_at=observed_at,
        available=True,
        http=ctx.last_fetched.detail if ctx.last_fetched else None,
        latest_event_at=newest,
        published_at=_ms(metadata["generated"]) if "generated" in metadata else None,
        count=count,
        count_label=source.volume.metric,
        fields=json_fields(rows) if rows else None,
        sample_parquet=sample,
        notes=(
            f"newest event {age_min:.0f} min ago, 24h count {count:,.0f}"
            if age_min is not None
            else f"24h count {count:,.0f}, no event times"
        ),
    )
