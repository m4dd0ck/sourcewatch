import json
import shutil
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from sourcewatch.checks import fingerprint
from sourcewatch.cli import app
from sourcewatch.clock import FixedClock
from sourcewatch.config import SchemaFile
from sourcewatch.observation import SchemaField
from sourcewatch.runner import format_table, run_all
from sourcewatch.store import DataStore
from sourcewatch.types import Status
from tests.conftest import FIXTURES, RUN_AT, SOURCES_DIR, load_response, no_sleep

CFPB = "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"


def mock_all(mock: respx.MockRouter) -> None:
    """Six sources: four healthy, TLC down (403 everywhere), 311 answering."""
    mock.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson").respond(
        200, json=load_response("usgs/all_day.json")
    )
    mock.get(url__startswith="https://archive-api.open-meteo.com").respond(
        200, json=load_response("open_meteo/archive.json")
    )
    mock.get(url__startswith="https://api.fiscaldata.treasury.gov").respond(
        200, json=load_response("treasury/latest.json")
    )
    mock.get(CFPB, params__contains={"sort": "created_date_desc"}).respond(
        200, json=load_response("cfpb/latest.json")
    )
    mock.get(CFPB, params__contains={"size": "0"}).respond(
        200, json=load_response("cfpb/count_day.json")
    )
    mock.get("https://data.cityofnewyork.us/api/views/erm2-nwe9.json").respond(
        200, json=load_response("socrata/views.json")
    )
    mock.get(
        "https://data.cityofnewyork.us/resource/erm2-nwe9.json",
        params__contains={"$select": "count(*)"},
    ).respond(200, json=load_response("socrata/count.json"))
    mock.get(
        "https://data.cityofnewyork.us/resource/erm2-nwe9.json", params__contains={"$limit": "200"}
    ).respond(200, json=load_response("socrata/sample.json"))
    mock.head(url__startswith="https://d37ci6vzurychx.cloudfront.net/").respond(403)


def sources_with_drift(tmp_path: Path) -> Path:
    """A copy of sources/ where nyc-311's accepted schema lacks a field the feed now has."""
    copy = tmp_path / "sources"
    shutil.copytree(SOURCES_DIR, copy)
    fields = [
        SchemaField(name="unique_key", dtype="text"),
        SchemaField(name="created_date", dtype="calendar_date"),
    ]
    (copy / "nyc-311.schema.json").write_text(
        SchemaFile(
            fields=fields, accepted_at=RUN_AT, fingerprint=fingerprint(fields)
        ).model_dump_json()
    )
    return copy


def test_full_run_with_one_source_down_and_one_drifted(
    mock: respx.MockRouter, client: httpx.Client, clock: FixedClock, data_dir: Path, tmp_path: Path
):
    mock_all(mock)
    sources_dir = sources_with_drift(tmp_path)

    report = run_all(sources_dir, data_dir, clock, client=client, env={}, sleep=no_sleep)

    statuses = report.statuses
    assert statuses["nyc-tlc"] == Status.FAILED
    assert statuses["nyc-311"] == Status.DEGRADED
    assert statuses["usgs-earthquakes"] == Status.OK and statuses["treasury-dts"] == Status.OK
    assert report.overall == Status.FAILED
    assert report.line == "1 source failed, 1 source degraded"

    table = format_table(report)
    assert "nyc-tlc                FAIL   -      -       -       -       failed" in table
    assert "nyc-311                ok     ok     ok      DRIFT   ok      degraded" in table
    assert "6 sources," in table and "wrote" in table

    store = DataStore(data_dir)
    history = store.read_history(1, RUN_AT)
    assert {o.run_id for o in history} == {report.run_id}
    assert len(history) == len(report.outcomes) and len(history) >= 6 * 4
    # The first failing run does not open an availability incident; schema drift opens at once.
    assert [(i.source_id, i.check) for i in store.read_incidents().open] == [("nyc-311", "schema")]
    last = store.read_last_run()
    assert last is not None and last.overall == Status.FAILED
    assert last.sources["nyc-tlc"].detail.startswith("no monthly file found")

    # A second run with the same failure opens the incident for TLC availability; the schema
    # drift opened on the first run.
    run_all(sources_dir, data_dir, clock, client=client, env={}, sleep=no_sleep)
    assert sorted((i.source_id, i.check) for i in store.read_incidents().open) == [
        ("nyc-311", "schema"),
        ("nyc-tlc", "availability"),
    ]


def test_only_one_source(
    mock: respx.MockRouter, client: httpx.Client, clock: FixedClock, data_dir: Path
):
    mock_all(mock)
    report = run_all(
        SOURCES_DIR, data_dir, clock, only="usgs-earthquakes", client=client, env={}, sleep=no_sleep
    )
    assert [r.source.id for r in report.reports] == ["usgs-earthquakes"]
    assert report.line == "All 1 sources healthy"


def test_cli_sources_validate_and_version():
    runner = CliRunner()
    result = runner.invoke(app, ["sources", "--sources", str(SOURCES_DIR)])
    assert result.exit_code == 0 and "nyc-tlc" in result.output and "monthly" in result.output
    result = runner.invoke(app, ["validate-config", "--sources", str(SOURCES_DIR)])
    assert result.exit_code == 0 and result.output.startswith("6 sources valid")
    assert runner.invoke(app, ["version"]).output.strip()


def test_cli_exit_codes(tmp_path: Path):
    runner = CliRunner()
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "x.yaml").write_text("id: x\n")
    result = runner.invoke(app, ["run", "--sources", str(bad), "--data-dir", str(tmp_path / "d")])
    assert result.exit_code == 2 and "error:" in result.output
    result = runner.invoke(
        app,
        [
            "run",
            "--sources",
            str(SOURCES_DIR),
            "--data-dir",
            str(tmp_path / "d"),
            "--source",
            "nope",
        ],
    )
    assert result.exit_code == 2 and "no source with id" in result.output


def test_cli_site_from_fixtures(tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "site"
    result = runner.invoke(
        app,
        [
            "site",
            "--sources",
            str(SOURCES_DIR),
            "--data-dir",
            str(FIXTURES / "data"),
            "--out",
            str(out),
            "--at",
            RUN_AT.isoformat(),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads((out / "status.json").read_text())["generated_at"] == RUN_AT.isoformat()
