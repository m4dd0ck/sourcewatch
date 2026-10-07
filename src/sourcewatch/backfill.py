"""One-off commands that need history: volume baselines from each source, and accept-schema."""

import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from sourcewatch import checks
from sourcewatch.cadence import last_day_of_month, shift_months
from sourcewatch.clock import Clock, local_date
from sourcewatch.config import (
    CfpbProbe,
    OpenMeteoProbe,
    ParquetRemoteProbe,
    SchemaFile,
    SocrataProbe,
    SourceConfig,
    TreasuryProbe,
    load_sources,
    write_expected_schema,
)
from sourcewatch.errors import ConfigError, ProbeError, StoreError
from sourcewatch.http import Sleeper, get_json, head, make_client
from sourcewatch.observation import SchemaField
from sourcewatch.probes.parquet_remote import read_footer
from sourcewatch.store import BaselineRow, DataStore


@dataclass(frozen=True)
class BackfillResult:
    source_id: str
    rows: int
    message: str


def _cfpb(
    source: SourceConfig,
    cfg: CfpbProbe,
    client: httpx.Client,
    today: date,
    days: int,
    sleep: Sleeper,
) -> list[BaselineRow]:
    rows = []
    for back in range(cfg.count_lag_days, cfg.count_lag_days + days):
        day = today - timedelta(days=back)
        body, _ = get_json(
            client,
            cfg.endpoint,
            params={
                "size": 0,
                "no_aggs": "true",
                "date_received_min": day.isoformat(),
                "date_received_max": day.isoformat(),
            },
            sleep=sleep,
        )
        total = ((body.get("hits") or {}).get("total") or {}).get("value")
        if total is not None:
            rows.append(
                BaselineRow(
                    source_id=source.id,
                    metric=source.volume.metric,
                    day=day,
                    value=float(total),
                    origin="backfill",
                )
            )
        sleep(0.2)
    return rows


def _socrata(
    source: SourceConfig,
    cfg: SocrataProbe,
    client: httpx.Client,
    today: date,
    days: int,
    sleep: Sleeper,
) -> list[BaselineRow]:
    start = today - timedelta(days=days + cfg.count_lag_days)
    end = today - timedelta(days=cfg.count_lag_days)
    body, _ = get_json(
        client,
        f"https://{cfg.domain}/resource/{cfg.dataset_id}.json",
        params={
            "$select": f"date_trunc_ymd({cfg.timestamp_field}) as day, count(*) as n",
            "$where": f"{cfg.timestamp_field} between '{start}T00:00:00' and '{end}T23:59:59'",
            "$group": "day",
            "$order": "day",
            "$limit": days + 5,
        },
        sleep=sleep,
    )
    return [
        BaselineRow(
            source_id=source.id,
            metric=source.volume.metric,
            day=date.fromisoformat(r["day"][:10]),
            value=float(r["n"]),
            origin="backfill",
        )
        for r in body
        if r.get("day") and r.get("n") is not None
    ]


def _treasury(
    source: SourceConfig, cfg: TreasuryProbe, client: httpx.Client, days: int, sleep: Sleeper
) -> list[BaselineRow]:
    body, _ = get_json(
        client,
        cfg.endpoint,
        params={"sort": "-record_date", "page[size]": min(days * 6, 1000)},
        sleep=sleep,
    )
    counts = Counter(str(r["record_date"]) for r in body.get("data") or [] if r.get("record_date"))
    dates = sorted(counts)
    # The oldest date on the page may be cut off mid-day; drop it.
    return [
        BaselineRow(
            source_id=source.id,
            metric=source.volume.metric,
            day=date.fromisoformat(d),
            value=float(counts[d]),
            origin="backfill",
        )
        for d in dates[1:]
    ]


def _open_meteo(
    source: SourceConfig,
    cfg: OpenMeteoProbe,
    client: httpx.Client,
    today: date,
    days: int,
    sleep: Sleeper,
) -> list[BaselineRow]:
    body, _ = get_json(
        client,
        cfg.endpoint,
        params={
            "latitude": cfg.latitude,
            "longitude": cfg.longitude,
            "start_date": (today - timedelta(days=days)).isoformat(),
            "end_date": (today - timedelta(days=1)).isoformat(),
            "hourly": cfg.hourly[0],
            "timezone": cfg.tz,
        },
        sleep=sleep,
    )
    hourly = body.get("hourly") or {}
    per_day: Counter[str] = Counter()
    for stamp, value in zip(
        hourly.get("time") or [], hourly.get(cfg.hourly[0]) or [], strict=False
    ):
        if value is not None:
            per_day[stamp[:10]] += 1
    return [
        BaselineRow(
            source_id=source.id,
            metric=source.volume.metric,
            day=date.fromisoformat(d),
            value=float(n),
            origin="backfill",
        )
        for d, n in sorted(per_day.items())
    ]


def _parquet_remote(
    source: SourceConfig,
    cfg: ParquetRemoteProbe,
    client: httpx.Client,
    today: date,
    sleep: Sleeper,
    months: int = 12,
) -> list[BaselineRow]:
    rows = []
    first = today.replace(day=1)
    for back in range(1, months + 1):
        month = shift_months(first, -back)
        url = cfg.base_url + cfg.pattern.format(yyyy=month.year, mm=f"{month.month:02d}")
        if not head(client, url, sleep=sleep).ok:
            continue
        num_rows, _ = read_footer(url)
        rows.append(
            BaselineRow(
                source_id=source.id,
                metric=source.volume.metric,
                day=last_day_of_month(month),
                value=float(num_rows),
                origin="backfill",
            )
        )
    return rows


def backfill_source(
    source: SourceConfig, client: httpx.Client, now: datetime, days: int, sleep: Sleeper
) -> list[BaselineRow]:
    cfg = source.probe
    today = local_date(now, source.cadence.tz)
    if isinstance(cfg, CfpbProbe):
        return _cfpb(source, cfg, client, now.astimezone(UTC).date(), days, sleep)
    if isinstance(cfg, SocrataProbe):
        return _socrata(source, cfg, client, today, days, sleep)
    if isinstance(cfg, TreasuryProbe):
        return _treasury(source, cfg, client, days, sleep)
    if isinstance(cfg, OpenMeteoProbe):
        return _open_meteo(source, cfg, client, today, days, sleep)
    if isinstance(cfg, ParquetRemoteProbe):
        return _parquet_remote(source, cfg, client, today, sleep)
    return []


def backfill_all(
    sources_dir: Path,
    data_dir: Path,
    clock: Clock,
    *,
    only: str | None = None,
    days: int = 56,
    client: httpx.Client | None = None,
    sleep: Sleeper = time.sleep,
) -> list[BackfillResult]:
    """Fetch each source's own history and merge it into ``state/baselines.parquet``."""
    sources = load_sources(sources_dir)
    if only is not None:
        sources = [s for s in sources if s.id == only]
        if not sources:
            raise ConfigError(f"no source with id {only!r} in {sources_dir}")
    store = DataStore(data_dir)
    existing = {(r.source_id, r.day): r for r in store.read_baselines()}
    results = []
    own = client is None
    http_client = client or make_client()
    now = clock.now()
    try:
        for source in sources:
            try:
                rows = backfill_source(source, http_client, now, days, sleep)
            except ProbeError as exc:
                results.append(BackfillResult(source.id, 0, f"failed: {exc}"))
                continue
            if not rows:
                results.append(
                    BackfillResult(source.id, 0, "no history available from this source")
                )
                continue
            for row in rows:
                existing[(row.source_id, row.day)] = row
            span = f"{rows[0].day} to {rows[-1].day}"
            results.append(BackfillResult(source.id, len(rows), f"{len(rows)} days ({span})"))
    finally:
        if own:
            http_client.close()
    if existing:
        store.write_baselines(sorted(existing.values(), key=lambda r: (r.source_id, r.day)))
    return results


def accept_schema_from_history(
    sources_dir: Path, data_dir: Path, source_id: str, clock: Clock
) -> Path:
    """Write the sidecar schema file from the newest schema outcome in history.

    Raises:
        ConfigError: if the source is unknown or history holds no schema for it.
    """
    sources = {s.id: s for s in load_sources(sources_dir)}
    if source_id not in sources:
        raise ConfigError(f"no source with id {source_id!r} in {sources_dir}")
    store = DataStore(data_dir)
    now = clock.now()
    try:
        outcomes = [
            o
            for o in store.read_history(90, now)
            if o.source_id == source_id and o.check == "schema"
        ]
    except StoreError as exc:
        raise ConfigError(str(exc)) from exc
    with_fields = [o for o in outcomes if o.details.get("fields")]
    if not with_fields:
        raise ConfigError(
            f"no observed schema for {source_id!r} in the last 90 days; run `sourcewatch run` first"
        )
    newest = max(with_fields, key=lambda o: o.run_at)
    fields = [SchemaField.model_validate(f) for f in newest.details["fields"]]
    schema = SchemaFile(fields=fields, accepted_at=now, fingerprint=checks.fingerprint(fields))
    return write_expected_schema(sources_dir, sources[source_id], schema)
