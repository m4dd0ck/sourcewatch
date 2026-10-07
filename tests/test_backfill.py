import json
from pathlib import Path

import httpx
import pytest
import respx

from sourcewatch.backfill import accept_schema_from_history, backfill_all
from sourcewatch.clock import FixedClock
from sourcewatch.config import load_expected_schema, load_sources
from sourcewatch.errors import ConfigError
from sourcewatch.outcome import Outcome
from sourcewatch.store import DataStore
from sourcewatch.types import Status
from tests.conftest import RUN_AT, SOURCES_DIR, load_response, no_sleep


def test_backfill_socrata_open_meteo_treasury_and_usgs_none(
    mock: respx.MockRouter, client: httpx.Client, clock: FixedClock, data_dir: Path
):
    mock.get(
        "https://data.cityofnewyork.us/resource/erm2-nwe9.json", params__contains={"$group": "day"}
    ).respond(
        200,
        json=[
            {"day": "2026-10-03T00:00:00.000", "n": "10100"},
            {"day": "2026-10-04T00:00:00.000", "n": "9900"},
        ],
    )
    mock.get(url__startswith="https://archive-api.open-meteo.com").respond(
        200, json=load_response("open_meteo/archive.json")
    )
    mock.get(url__startswith="https://api.fiscaldata.treasury.gov").respond(
        200, json=load_response("treasury/latest.json")
    )
    results = {
        r.source_id: r
        for r in [
            *backfill_all(
                SOURCES_DIR, data_dir, clock, only="nyc-311", client=client, sleep=no_sleep
            ),
            *backfill_all(
                SOURCES_DIR,
                data_dir,
                clock,
                only="open-meteo-archive",
                client=client,
                sleep=no_sleep,
            ),
            *backfill_all(
                SOURCES_DIR, data_dir, clock, only="treasury-dts", client=client, sleep=no_sleep
            ),
            *backfill_all(
                SOURCES_DIR, data_dir, clock, only="usgs-earthquakes", client=client, sleep=no_sleep
            ),
        ]
    }
    assert results["nyc-311"].rows == 2 and "2026-10-03 to 2026-10-04" in results["nyc-311"].message
    assert results["open-meteo-archive"].rows >= 10
    assert results["treasury-dts"].rows == 1  # the oldest (possibly partial) date is dropped
    assert results["usgs-earthquakes"].message == "no history available from this source"
    rows = DataStore(data_dir).read_baselines()
    assert {r.source_id for r in rows} == {"nyc-311", "open-meteo-archive", "treasury-dts"}
    assert all(r.origin == "backfill" for r in rows)


def test_backfill_reports_a_failed_source_and_keeps_going(
    mock: respx.MockRouter, client: httpx.Client, clock: FixedClock, data_dir: Path
):
    mock.get(url__startswith="https://data.cityofnewyork.us").respond(503)
    [result] = backfill_all(
        SOURCES_DIR, data_dir, clock, only="nyc-311", client=client, sleep=no_sleep
    )
    assert result.rows == 0 and result.message.startswith("failed:")


def test_accept_schema_from_history(tmp_path: Path, clock: FixedClock, data_dir: Path):
    sources_dir = tmp_path / "sources"
    import shutil

    shutil.copytree(SOURCES_DIR, sources_dir)
    store = DataStore(data_dir)
    fields = [{"name": "mag", "dtype": "DOUBLE"}, {"name": "place", "dtype": "VARCHAR"}]
    store.write_outcomes(
        [
            Outcome(
                run_id="r1",
                run_at=RUN_AT,
                source_id="usgs-earthquakes",
                check="schema",
                status=Status.OK,
                message="2 fields",
                details={"fields": fields},
            ),
        ]
    )
    path = accept_schema_from_history(sources_dir, data_dir, "usgs-earthquakes", clock)
    assert path == sources_dir / "usgs-earthquakes.schema.json"
    saved = json.loads(path.read_text())
    assert [f["name"] for f in saved["fields"]] == ["mag", "place"] and saved["fingerprint"]
    source = next(s for s in load_sources(sources_dir) if s.id == "usgs-earthquakes")
    assert load_expected_schema(sources_dir, source) is not None
    with pytest.raises(ConfigError, match="no observed schema"):
        accept_schema_from_history(sources_dir, data_dir, "nyc-tlc", clock)
    with pytest.raises(ConfigError, match="no source with id"):
        accept_schema_from_history(sources_dir, data_dir, "nope", clock)
