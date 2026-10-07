import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from sourcewatch.charts import sparkline_svg, strip_svg
from sourcewatch.clock import FixedClock
from sourcewatch.errors import UnsafeOutputError
from sourcewatch.site import build_site
from sourcewatch.types import Status
from tests.conftest import FIXTURES, RUN_AT, SOURCES_DIR

FIXTURE_DATA = FIXTURES / "data"


def test_strip_has_one_cell_per_day_and_escapes_titles():
    days = [
        (date(2026, 10, 1) + timedelta(days=i), s)
        for i, s in enumerate([Status.OK, None, Status.FAILED])
    ]
    svg = strip_svg(days, label="<x>")
    assert svg.count("<rect") == 3 and 'class="none"' in svg and 'class="failed"' in svg
    assert "&lt;x&gt;" in svg and "2026-10-02: no observation" in svg


def test_sparkline_breaks_at_gaps_and_draws_band():
    pts = [
        (date(2026, 10, 1) + timedelta(days=i), v) for i, v in enumerate([1.0, 2.0, None, 3.0, 4.0])
    ]
    svg = sparkline_svg(pts, band=(1.0, 3.0))
    assert svg.count("<polyline") == 2 and 'class="band"' in svg and "2026-10-05: 4" in svg
    assert sparkline_svg([(date(2026, 10, 1), None)]).count("<polyline") == 0


def test_fixture_site_renders_everything(tmp_path: Path, clock: FixedClock):
    out = tmp_path / "site"
    index = build_site(SOURCES_DIR, FIXTURE_DATA, out, clock)
    assert index == out / "index.html"
    html = index.read_text()
    for sid in ("nyc-tlc", "cfpb-complaints", "nyc-311", "usgs-earthquakes"):
        assert f"sources/{sid}.html" in html
        assert (out / "sources" / f"{sid}.html").exists()
    assert "source degraded" in html or "healthy" in html
    status = json.loads((out / "status.json").read_text())
    assert status["generated_at"] == RUN_AT.isoformat()
    assert [s["id"] for s in status["sources"]] == [
        "cfpb-complaints",
        "nyc-311",
        "nyc-tlc",
        "open-meteo-archive",
        "treasury-dts",
        "usgs-earthquakes",
    ]
    cfpb = next(s for s in status["sources"] if s["id"] == "cfpb-complaints")
    assert cfpb["status"] == "degraded" and cfpb["open_incidents"]
    history = json.loads((out / "history.json").read_text())
    assert len(history["days"]) == 90 and history["days"][-1] == "2026-10-07"
    assert history["sources"]["nyc-311"].count(None) >= 1  # the skipped morning
    assert "failed" in history["sources"]["nyc-311"]  # the outage
    incidents = (out / "incidents.html").read_text()
    assert "nyc-311" in incidents and "complaint_what_happened_redacted" in incidents
    about = (out / "about.html").read_text()
    assert "Requests per run" in about


def test_site_build_is_deterministic(tmp_path: Path, clock: FixedClock):
    a = build_site(SOURCES_DIR, FIXTURE_DATA, tmp_path / "a", clock).read_text()
    b = build_site(SOURCES_DIR, FIXTURE_DATA, tmp_path / "b", clock).read_text()
    assert a == b


def test_output_guard(tmp_path: Path, clock: FixedClock):
    out = tmp_path / "precious"
    out.mkdir()
    (out / "notes.txt").write_text("mine")
    with pytest.raises(UnsafeOutputError):
        build_site(SOURCES_DIR, FIXTURE_DATA, out, clock)
    assert (out / "notes.txt").exists()


def test_site_renders_with_an_empty_data_dir(tmp_path: Path, clock: FixedClock):
    index = build_site(SOURCES_DIR, tmp_path / "empty", tmp_path / "site", clock)
    html = index.read_text()
    assert "no observations yet" in html
