from datetime import UTC, datetime

import respx

from sourcewatch.config import SourceConfig
from sourcewatch.probes import PROBES
from sourcewatch.probes.base import ProbeContext
from tests.conftest import load_response


def test_usgs_probe(
    mock: respx.MockRouter, ctx: ProbeContext, source_by_id: dict[str, SourceConfig]
):
    source = source_by_id["usgs-earthquakes"]
    mock.get(source.probe.feed_url).respond(200, json=load_response("usgs/all_day.json"))  # type: ignore[union-attr]

    obs = PROBES["usgs"](source, ctx)

    assert obs.available and ctx.requests_made == 1
    assert obs.count == 267 and obs.count_label == "events in the past 24 hours"
    assert obs.latest_event_at == datetime.fromtimestamp(1791397396366 / 1000, tz=UTC)
    assert obs.published_at == datetime.fromtimestamp(1791398641, tz=UTC)
    names = {f.name for f in obs.fields or []}
    assert {"mag", "place", "time", "longitude", "latitude", "depth_km"} <= names
    assert obs.sample_parquet is not None and obs.sample_parquet.exists()
    assert obs.notes.startswith("newest event") and "24h count 267" in obs.notes


def test_open_meteo_probe(
    mock: respx.MockRouter, ctx: ProbeContext, source_by_id: dict[str, SourceConfig]
):
    source = source_by_id["open-meteo-archive"]
    route = mock.get(url__startswith="https://archive-api.open-meteo.com/v1/archive").respond(
        200, json=load_response("open_meteo/archive.json")
    )

    obs = PROBES["open_meteo"](source, ctx)

    params = route.calls.last.request.url.params
    assert params["start_date"] == "2026-09-27" and params["end_date"] == "2026-10-07"
    assert params["timezone"] == "America/New_York"
    assert obs.available and ctx.requests_made == 1
    # 07:03 ET on 2026-10-07: the newest daily value is for the 7th, yesterday had 24 hourly rows.
    assert obs.latest_event_at == datetime(2026, 10, 7, 4, tzinfo=UTC)
    assert obs.count == 24
    assert [f.name for f in obs.fields or []] == [
        "time",
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_sum",
    ]
    assert obs.notes == "archive through 2026-10-07, 24 hourly rows on 2026-10-06"


def test_open_meteo_error_body_is_a_probe_error(
    mock: respx.MockRouter, ctx: ProbeContext, source_by_id: dict[str, SourceConfig]
):
    import pytest

    from sourcewatch.errors import ProbeError

    mock.get(url__startswith="https://archive-api.open-meteo.com").respond(
        200, json={"error": True, "reason": "Parameter 'daily' is not valid"}
    )
    with pytest.raises(ProbeError, match="not valid"):
        PROBES["open_meteo"](source_by_id["open-meteo-archive"], ctx)
