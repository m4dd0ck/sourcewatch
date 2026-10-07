"""Monthly Parquet files on a public bucket (NYC TLC): HEAD to find the newest, footer for facts."""

from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import duckdb

from sourcewatch.cadence import last_day_of_month, shift_months
from sourcewatch.clock import local_date
from sourcewatch.config import ParquetRemoteProbe, SourceConfig
from sourcewatch.errors import ProbeError
from sourcewatch.observation import Observation, SchemaField
from sourcewatch.probes.base import ProbeContext

# Reason: tests point the footer read at a local file while the HEAD walk stays mocked.
Locator = Callable[[str], str]


def read_footer(location: str) -> tuple[int, list[SchemaField]]:
    """Row count and leaf schema from a Parquet footer, remote (httpfs) or local."""
    con = duckdb.connect()
    try:
        if location.startswith(("http://", "https://")):
            con.execute("INSTALL httpfs; LOAD httpfs;")
        row = con.execute(
            "SELECT num_rows FROM parquet_file_metadata($p::VARCHAR)", {"p": location}
        ).fetchone()
        schema = con.execute(
            "SELECT name, coalesce(converted_type, type) FROM parquet_schema($p::VARCHAR) "
            "WHERE num_children IS NULL",
            {"p": location},
        ).fetchall()
    except duckdb.Error as exc:
        raise ProbeError(f"could not read Parquet footer at {location}: {exc}") from exc
    finally:
        con.close()
    if row is None:
        raise ProbeError(f"Parquet footer at {location} had no row count")
    return int(row[0]), [SchemaField(name=str(n), dtype=str(t)) for n, t in schema]


def probe(
    source: SourceConfig, ctx: ProbeContext, locator: Locator = lambda url: url
) -> Observation:
    cfg = source.probe
    assert isinstance(cfg, ParquetRemoteProbe)
    observed_at = ctx.now()
    this_month = local_date(observed_at, cfg.tz).replace(day=1)
    tz = ZoneInfo(cfg.tz)

    for back in range(1, cfg.lookback_months + 1):
        month = shift_months(this_month, -back)
        url = cfg.base_url + cfg.pattern.format(yyyy=month.year, mm=f"{month.month:02d}")
        answer = ctx.head(url)
        if answer.ok:
            published_raw = answer.headers.get("last-modified")
            published = (
                parsedate_to_datetime(published_raw).astimezone(UTC) if published_raw else None
            )
            num_rows, fields = read_footer(locator(url))
            period_end = last_day_of_month(month)
            return Observation(
                source_id=source.id,
                observed_at=observed_at,
                available=True,
                http=answer.detail,
                latest_event_at=datetime.combine(period_end, datetime.min.time(), tz).astimezone(
                    UTC
                ),
                latest_period=month,
                published_at=published,
                count=float(num_rows),
                count_label=source.volume.metric,
                fields=fields,
                notes=(
                    f"yellow {month:%Y-%m} published {published:%Y-%m-%d}"
                    if published
                    else f"yellow {month:%Y-%m} present"
                ),
            )
        if answer.detail.status not in (403, 404):
            return Observation(
                source_id=source.id,
                observed_at=observed_at,
                available=False,
                unavailable_reason=f"HEAD {url} returned HTTP {answer.detail.status}",
                http=answer.detail,
            )

    return Observation(
        source_id=source.id,
        observed_at=observed_at,
        available=False,
        unavailable_reason=f"no monthly file found in the last {cfg.lookback_months} months",
        http=ctx.last_fetched.detail if ctx.last_fetched else None,
    )
