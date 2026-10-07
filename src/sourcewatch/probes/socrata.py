"""A Socrata (SODA) dataset, such as NYC 311 on data.cityofnewyork.us."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sourcewatch.clock import local_date
from sourcewatch.config import SocrataProbe, SourceConfig
from sourcewatch.errors import ProbeError
from sourcewatch.observation import Observation, SchemaField
from sourcewatch.probes.base import ProbeContext, write_sample


def probe(source: SourceConfig, ctx: ProbeContext) -> Observation:
    cfg = source.probe
    assert isinstance(cfg, SocrataProbe)
    observed_at = ctx.now()
    tz = ZoneInfo(cfg.tz)
    headers = {}
    token = ctx.env.get(cfg.app_token_env)
    if token:
        headers["X-App-Token"] = token

    meta = ctx.get_json(f"https://{cfg.domain}/api/views/{cfg.dataset_id}.json", headers=headers)
    published = meta.get("rowsUpdatedAt")
    columns = meta.get("columns") or []
    fields = [
        SchemaField(name=str(c.get("fieldName")), dtype=str(c.get("dataTypeName", "")))
        for c in columns
        if c.get("fieldName")
    ]

    resource = f"https://{cfg.domain}/resource/{cfg.dataset_id}.json"
    day = local_date(observed_at, cfg.tz) - timedelta(days=cfg.count_lag_days)
    where = (
        f"{cfg.timestamp_field} between '{day.isoformat()}T00:00:00' "
        f"and '{day.isoformat()}T23:59:59'"
    )
    counted = ctx.get_json(
        resource, params={"$select": "count(*)", "$where": where}, headers=headers
    )
    count = float(counted[0]["count"]) if counted and "count" in counted[0] else None

    sample_rows: list[dict[str, Any]] = ctx.get_json(
        resource,
        params={"$limit": cfg.sample_size, "$order": f"{cfg.timestamp_field} DESC"},
        headers=headers,
    )
    if not sample_rows:
        raise ProbeError("Socrata returned no rows")
    newest_raw = sample_rows[0].get(cfg.timestamp_field)
    newest = (
        datetime.fromisoformat(str(newest_raw)).replace(tzinfo=tz).astimezone(UTC)
        if newest_raw
        else None
    )
    sample = write_sample(sample_rows, ctx.workdir / f"{source.id}.parquet")
    return Observation(
        source_id=source.id,
        observed_at=observed_at,
        available=True,
        http=ctx.last_fetched.detail if ctx.last_fetched else None,
        latest_event_at=newest,
        published_at=datetime.fromtimestamp(int(published), tz=UTC) if published else None,
        count=count,
        count_label=source.volume.metric,
        fields=fields or None,
        sample_parquet=sample,
        notes=(
            f"latest {newest.astimezone(tz):%Y-%m-%d %H:%M} {newest.astimezone(tz):%Z}, "
            f"{count:,.0f} on {day}"
            if newest and count is not None
            else "latest timestamp or count unavailable"
        ),
    )
