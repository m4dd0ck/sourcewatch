from datetime import UTC, date, datetime

import respx

from sourcewatch.config import SourceConfig
from sourcewatch.probes import parquet_remote
from sourcewatch.probes.base import ProbeContext
from tests.conftest import RESPONSES

BASE = "https://d37ci6vzurychx.cloudfront.net/trip-data/"
LOCAL = str(RESPONSES / "tlc" / "yellow_sample.parquet")


def test_walks_back_to_the_newest_month_and_reads_only_the_footer(
    mock: respx.MockRouter, ctx: ProbeContext, source_by_id: dict[str, SourceConfig]
):
    mock.head(BASE + "yellow_tripdata_2026-09.parquet").respond(404)
    mock.head(BASE + "yellow_tripdata_2026-08.parquet").respond(
        200,
        headers={"last-modified": "Thu, 01 Oct 2026 13:38:15 GMT", "content-length": "59043961"},
    )

    obs = parquet_remote.probe(source_by_id["nyc-tlc"], ctx, locator=lambda url: LOCAL)

    assert obs.available and ctx.requests_made == 2
    assert obs.latest_period == date(2026, 8, 1)
    assert obs.latest_event_at == datetime(2026, 8, 31, 4, tzinfo=UTC)  # Aug 31 midnight ET
    assert obs.published_at == datetime(2026, 10, 1, 13, 38, 15, tzinfo=UTC)
    assert obs.count == 5  # the local fixture's footer
    names = [f.name for f in obs.fields or []]
    assert len(names) == 21 and names[0] == "VendorID" and "request_source" in names
    assert obs.notes == "yellow 2026-08 published 2026-10-01"


def test_every_month_missing_is_unavailable_within_budget(
    mock: respx.MockRouter, ctx: ProbeContext, source_by_id: dict[str, SourceConfig]
):
    mock.head(url__startswith=BASE).respond(403)
    ctx.budget = 5
    obs = parquet_remote.probe(source_by_id["nyc-tlc"], ctx, locator=lambda url: LOCAL)
    assert not obs.available and ctx.requests_made == 4
    assert "no monthly file found in the last 4 months" in obs.unavailable_reason


def test_server_error_stops_the_walk(
    mock: respx.MockRouter, ctx: ProbeContext, source_by_id: dict[str, SourceConfig]
):
    mock.head(url__startswith=BASE).respond(500)
    obs = parquet_remote.probe(source_by_id["nyc-tlc"], ctx, locator=lambda url: LOCAL)
    assert not obs.available and ctx.requests_made == 1
    assert obs.http is not None and obs.http.status == 500 and obs.http.attempts == 3
    assert "HTTP 500" in obs.unavailable_reason


def test_read_footer_errors_become_probe_errors(tmp_path):
    import pytest

    from sourcewatch.errors import ProbeError

    with pytest.raises(ProbeError, match="could not read Parquet footer"):
        parquet_remote.read_footer(str(tmp_path / "missing.parquet"))
