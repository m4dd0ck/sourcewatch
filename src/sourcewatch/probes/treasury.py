"""Treasury Fiscal Data API: the Daily Treasury Statement operating cash balance."""

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sourcewatch.config import SourceConfig, TreasuryProbe
from sourcewatch.errors import ProbeError
from sourcewatch.observation import Observation, SchemaField
from sourcewatch.probes.base import ProbeContext, write_sample

EASTERN = ZoneInfo("America/New_York")


def probe(source: SourceConfig, ctx: ProbeContext) -> Observation:
    cfg = source.probe
    assert isinstance(cfg, TreasuryProbe)
    observed_at = ctx.now()
    data = ctx.get_json(cfg.endpoint, params={"sort": "-record_date", "page[size]": cfg.page_size})
    raw_rows: list[dict[str, Any]] = data.get("data") or []
    meta = data.get("meta") or {}
    if not raw_rows:
        raise ProbeError("Fiscal Data returned no rows")
    # Reason: Fiscal Data encodes a missing value as the string "null"; keep it a real null so
    # completeness checks mean something.
    rows = [{k: (None if v == "null" else v) for k, v in r.items()} for r in raw_rows]

    dates = sorted({str(r["record_date"]) for r in rows if r.get("record_date")}, reverse=True)
    newest = date.fromisoformat(dates[0])
    rows_on_newest = sum(1 for r in rows if r.get("record_date") == dates[0])
    data_types: dict[str, str] = meta.get("dataTypes") or {}
    fields = [SchemaField(name=k, dtype=str(v)) for k, v in data_types.items()] or [
        SchemaField(name=k, dtype="STRING") for k in rows[0]
    ]
    sample = write_sample(rows, ctx.workdir / f"{source.id}.parquet")
    return Observation(
        source_id=source.id,
        observed_at=observed_at,
        available=True,
        http=ctx.last_fetched.detail if ctx.last_fetched else None,
        latest_event_at=datetime.combine(newest, datetime.min.time(), EASTERN).astimezone(UTC),
        count=float(rows_on_newest),
        count_label=source.volume.metric,
        fields=fields,
        sample_parquet=sample,
        notes=f"DTS record_date {newest} ({newest:%a}), {rows_on_newest} rows",
    )
