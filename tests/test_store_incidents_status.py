from datetime import timedelta
from pathlib import Path

from sourcewatch import incidents
from sourcewatch.incidents import IncidentBook
from sourcewatch.outcome import Outcome
from sourcewatch.status import effective_status, overall_line, overall_status, source_status
from sourcewatch.store import BaselineRow, DataStore, LastRun, SourceSummary
from sourcewatch.types import Severity, Status
from tests.conftest import RUN_AT


def outcome(source: str, check: str, status: Status, run_id: str = "r1", **kw: object) -> Outcome:
    base: dict[str, object] = {
        "run_id": run_id,
        "run_at": RUN_AT,
        "source_id": source,
        "check": check,
        "status": status,
        "message": f"{check} {status.value}",
    }
    base.update(kw)
    return Outcome.model_validate(base)


def test_store_round_trip_and_same_day_merge(data_dir: Path):
    store = DataStore(data_dir)
    first = [outcome("a", "availability", Status.OK, metric=120.0, details={"x": 1})]
    path = store.write_outcomes(first)
    assert path == data_dir / "history" / "2026" / "10" / "07.parquet"
    later = RUN_AT + timedelta(hours=3)
    second = [outcome("a", "availability", Status.FAILED, run_id="r2", run_at=later)]
    store.write_outcomes(second)
    rows = store.read_history(90, later)
    assert [(r.run_id, r.status) for r in rows] == [("r1", Status.OK), ("r2", Status.FAILED)]
    assert rows[0].details == {"x": 1} and rows[0].metric == 120.0
    assert rows[0].run_at == RUN_AT
    # rewriting the same run_id replaces, not duplicates
    store.write_outcomes([outcome("a", "availability", Status.DEGRADED, run_id="r2", run_at=later)])
    assert [r.status for r in store.read_history(90, later)] == [Status.OK, Status.DEGRADED]


def test_previous_run_and_volume_samples(data_dir: Path):
    store = DataStore(data_dir)
    day1 = RUN_AT - timedelta(days=1)
    store.write_outcomes(
        [outcome("a", "volume", Status.OK, run_id="r0", run_at=day1, metric=100.0)]
    )
    store.write_outcomes([outcome("a", "volume", Status.OK, run_id="r1", metric=110.0)])
    store.write_baselines(
        [
            BaselineRow(
                source_id="a",
                metric="m",
                day=day1.date() - timedelta(days=1),
                value=90.0,
                origin="backfill",
            ),
            BaselineRow(source_id="a", metric="m", day=day1.date(), value=1.0, origin="backfill"),
        ]
    )
    samples = store.volume_samples("a", RUN_AT)
    # history beats a backfilled value for the same day
    assert [(s.day.isoformat(), s.value) for s in samples] == [
        ("2026-10-05", 90.0),
        ("2026-10-06", 100.0),
        ("2026-10-07", 110.0),
    ]
    previous = store.read_previous_run("r1", RUN_AT)
    assert [o.run_id for o in previous] == ["r0"]
    assert store.read_previous_run("r0", day1) == []


def test_state_files_round_trip(data_dir: Path):
    store = DataStore(data_dir)
    assert store.read_incidents() == IncidentBook() and store.read_last_run() is None
    last = LastRun(
        run_id="r1",
        run_at=RUN_AT,
        overall=Status.OK,
        line="All 1 sources healthy",
        sources={"a": SourceSummary(status=Status.OK, detail="fine")},
    )
    store.write_last_run(last)
    assert store.read_last_run() == last


def test_incident_opens_on_second_consecutive_failure_and_resolves():
    book = IncidentBook()
    first_fail = [outcome("nyc-311", "availability", Status.FAILED)]
    opened, resolved = incidents.apply(book, first_fail, [], RUN_AT)
    assert opened == [] and book.open == []
    t2 = RUN_AT + timedelta(days=1)
    opened, resolved = incidents.apply(book, first_fail, first_fail, t2)
    assert len(opened) == 1 and opened[0].id == 1 and opened[0].opened_at == t2
    t3 = t2 + timedelta(days=1)
    incidents.apply(book, first_fail, first_fail, t3)
    assert book.open[0].runs == 2
    opened, resolved = incidents.apply(
        book, [outcome("nyc-311", "availability", Status.OK)], first_fail, t3
    )
    assert (
        resolved
        and resolved[0].resolved_at == t3
        and resolved[0].last_message.startswith("resolved:")
    )
    assert book.open == []


def test_failed_freshness_and_schema_drift_open_immediately():
    book = IncidentBook()
    now = RUN_AT
    outs = [
        outcome("nyc-311", "freshness", Status.FAILED),
        outcome("cfpb-complaints", "schema", Status.DEGRADED),
        outcome("usgs-earthquakes", "volume", Status.DEGRADED),
    ]
    opened, _ = incidents.apply(book, outs, [], now)
    assert sorted(i.check for i in opened) == ["freshness", "schema"]
    # schema resolves only when ok again
    still = [outcome("cfpb-complaints", "schema", Status.DEGRADED)]
    incidents.apply(book, still, outs, now)
    assert book.open_for(("cfpb-complaints", "schema")) is not None
    _, resolved = incidents.apply(
        book, [outcome("cfpb-complaints", "schema", Status.OK)], still, now
    )
    assert [i.check for i in resolved] == ["schema"]


def test_status_roll_up():
    crit_fail = outcome("a", "freshness", Status.FAILED, severity=Severity.CRITICAL)
    warn_fail = outcome("a", "values:x", Status.FAILED, severity=Severity.WARNING)
    assert effective_status(warn_fail) == Status.DEGRADED
    assert source_status([outcome("a", "availability", Status.OK), warn_fail]) == Status.DEGRADED
    assert source_status([crit_fail, outcome("a", "volume", Status.UNKNOWN)]) == Status.FAILED
    statuses = {"a": Status.OK, "b": Status.DEGRADED, "c": Status.FAILED, "d": Status.UNKNOWN}
    assert overall_status(statuses) == Status.FAILED
    assert overall_line(statuses) == "1 source failed, 1 source unknown, 1 source degraded"
    assert overall_line({"a": Status.OK, "b": Status.OK}) == "All 2 sources healthy"
    assert (
        overall_line({"a": Status.OK, "b": Status.DEGRADED, "c": Status.DEGRADED})
        == "2 sources degraded"
    )
