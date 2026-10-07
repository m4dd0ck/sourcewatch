from datetime import UTC, date, datetime, timedelta

from sourcewatch import checks
from sourcewatch.baselines import Sample
from sourcewatch.config import SchemaFile, SourceConfig
from sourcewatch.observation import HttpDetail, Observation, SchemaField
from sourcewatch.types import Severity, Status
from tests.conftest import RUN_AT


def observation(**kwargs: object) -> Observation:
    base: dict[str, object] = {
        "source_id": "usgs-earthquakes",
        "observed_at": RUN_AT,
        "available": True,
    }
    base.update(kwargs)
    return Observation.model_validate(base)


def test_availability_ok_and_failed(source_by_id: dict[str, SourceConfig]):
    src = source_by_id["usgs-earthquakes"]
    ok = checks.availability(src, observation(http=HttpDetail(url="u", status=200, latency_ms=120)))
    assert ok.status == Status.OK and ok.severity == Severity.CRITICAL and "120 ms" in ok.message
    down = checks.availability(
        src,
        observation(
            available=False,
            unavailable_reason="GET u returned HTTP 503",
            http=HttpDetail(url="u", status=503, latency_ms=7000, attempts=3),
        ),
    )
    assert down.status == Status.FAILED
    assert down.message == "HTTP 503 after 3 attempts: GET u returned HTTP 503"


def test_unobserved_is_unknown(source_by_id: dict[str, SourceConfig]):
    out = checks.unobserved(
        source_by_id["usgs-earthquakes"],
        observation(available=False, unavailable_reason="403"),
        "freshness",
    )
    assert out.status == Status.UNKNOWN and out.message == "not observed: 403"


def test_volume_learning_then_banded(source_by_id: dict[str, SourceConfig]):
    src = source_by_id["usgs-earthquakes"]  # band 0.4–2.5, min_samples 7
    obs = observation(count=300, count_label="events in the past 24 hours")
    learning = checks.volume(src, obs, [], RUN_AT)
    assert learning.status == Status.OK and "learning, 0/7" in learning.message

    history = [Sample(RUN_AT.date() - timedelta(days=i), 250 + i) for i in range(1, 11)]
    ok = checks.volume(src, obs, history, RUN_AT)
    assert ok.status == Status.OK and "within band" in ok.message and ok.details["samples"] == 10

    spike = checks.volume(src, observation(count=5000, count_label="x"), history, RUN_AT)
    assert spike.status == Status.DEGRADED and "above band" in spike.message
    zero = checks.volume(src, observation(count=0, count_label="x"), history, RUN_AT)
    assert zero.status == Status.FAILED
    none = checks.volume(src, observation(count=None), history, RUN_AT)
    assert none.status == Status.UNKNOWN


def test_weekday_aware_volume_compares_like_with_like(source_by_id: dict[str, SourceConfig]):
    src = source_by_id["cfpb-complaints"]  # weekday_aware, min_samples 7
    today = RUN_AT  # a Wednesday
    history = []
    for i in range(1, 71):
        day = today.date() - timedelta(days=i)
        history.append(Sample(day, 15000.0 if day.weekday() < 5 else 3000.0))
    weekend_like = checks.volume(src, observation(count=3100, count_label="x"), history, today)
    assert weekend_like.status == Status.DEGRADED and weekend_like.details["weekday_matched"]
    assert weekend_like.details["median"] == 15000.0


def test_schema_states(source_by_id: dict[str, SourceConfig]):
    src = source_by_id["cfpb-complaints"]
    fields = [
        SchemaField(name="product", dtype="VARCHAR"),
        SchemaField(name="company", dtype="VARCHAR"),
    ]
    none = checks.schema(src, observation(fields=fields), None)
    assert none.status == Status.OK and "no accepted schema yet" in none.message

    accepted = SchemaFile(fields=fields, accepted_at=RUN_AT, fingerprint=checks.fingerprint(fields))
    same = checks.schema(src, observation(fields=list(reversed(fields))), accepted)
    assert same.status == Status.OK and same.message == "schema unchanged (2 fields)"

    changed = [
        SchemaField(name="product", dtype="VARCHAR"),
        SchemaField(name="complaint_what_happened_redacted", dtype="VARCHAR"),
    ]
    drift = checks.schema(src, observation(fields=changed), accepted)
    assert drift.status == Status.DEGRADED
    assert drift.message == "schema changed: +complaint_what_happened_redacted, -company"
    retyped = checks.schema(
        src, observation(fields=[fields[0], SchemaField(name="company", dtype="BIGINT")]), accepted
    )
    assert "~company:VARCHAR→BIGINT" in retyped.message
    assert checks.schema(src, observation(fields=[]), accepted).status == Status.UNKNOWN


def test_fingerprint_is_order_independent():
    a = [SchemaField(name="x", dtype="INT"), SchemaField(name="y", dtype="VARCHAR")]
    assert checks.fingerprint(a) == checks.fingerprint(list(reversed(a)))
    assert checks.fingerprint(a) != checks.fingerprint(a[:1])


def test_freshness_outcome_carries_cadence_details(source_by_id: dict[str, SourceConfig]):
    src = source_by_id["treasury-dts"]
    obs = observation(
        source_id="treasury-dts", latest_event_at=datetime(2026, 10, 5, 4, tzinfo=UTC)
    )
    out = checks.freshness(src, obs, RUN_AT)
    assert (
        out.status == Status.OK
        and out.unit == "business days"
        and out.details["cadence"] == "business_days"
    )
    assert out.details["reference"] == date(2026, 10, 6).isoformat()


def test_json_fields_are_coarse_and_null_is_unknown_not_drift():
    from sourcewatch.probes.base import json_fields

    rows = [
        {"mag": 1.2, "gap": 120, "tags": None, "place": "x", "ok": True, "geo": {"a": 1}},
        {"mag": 2, "gap": 95.5, "tags": None, "place": "y", "ok": False, "geo": {"a": 2}},
    ]
    fields = {f.name: f.dtype for f in json_fields(rows)}
    assert fields == {
        "mag": "number",
        "gap": "number",
        "tags": "null",
        "place": "string",
        "ok": "boolean",
        "geo": "object",
    }
    before = [SchemaField(name="tags", dtype="null"), SchemaField(name="gap", dtype="number")]
    after = [SchemaField(name="tags", dtype="string"), SchemaField(name="gap", dtype="number")]
    assert checks.schema_diff(before, after) == []
    assert checks.schema_diff(after, [SchemaField(name="tags", dtype="number"), after[1]]) == [
        "~tags:string→number"
    ]
