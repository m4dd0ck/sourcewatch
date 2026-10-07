"""Generate tests/fixtures/data: 90 days of plausible synthetic history for the six sources.

Run with ``uv run python -m tests.make_fixture_data``; the output is committed so that CI and
the site tests render deterministically without the network.
"""

import random
import shutil
from datetime import timedelta
from pathlib import Path

from sourcewatch import incidents
from sourcewatch.config import load_sources
from sourcewatch.outcome import Outcome
from sourcewatch.store import DataStore, LastRun, SourceSummary
from sourcewatch.types import Severity, Status
from tests.conftest import RUN_AT, SOURCES_DIR

OUT = Path(__file__).resolve().parent / "fixtures" / "data"
DAYS = 90
SKIPPED_DAYS = {44}  # a morning the cron never fired
OUTAGE = {"nyc-311": range(60, 63)}  # three mornings of HTTP 503
DRIFT_FROM = {"cfpb-complaints": 81}  # a new field appears and stays
BASE_COUNT = {
    "cfpb-complaints": 13000,
    "nyc-311": 11000,
    "nyc-tlc": 3_300_000,
    "open-meteo-archive": 24,
    "treasury-dts": 4,
    "usgs-earthquakes": 280,
}


def _outcomes(rng: random.Random, source_id: str, day_index: int, run_at) -> list[Outcome]:
    def o(check: str, status: Status, msg: str, **kw: object) -> Outcome:
        return Outcome(
            run_at=run_at, source_id=source_id, check=check, status=status, message=msg, **kw
        )

    if day_index in OUTAGE.get(source_id, range(0)):
        return [
            o(
                "availability",
                Status.FAILED,
                "HTTP 503 after 3 attempts: GET returned HTTP 503",
                severity=Severity.CRITICAL,
                metric=6100.0,
                unit="ms",
            ),
            o("freshness", Status.UNKNOWN, "not observed: HTTP 503"),
            o("volume", Status.UNKNOWN, "not observed: HTTP 503"),
            o("schema", Status.UNKNOWN, "not observed: HTTP 503"),
        ]
    base = BASE_COUNT[source_id]
    weekend = run_at.weekday() >= 5 and source_id in ("cfpb-complaints", "nyc-311")
    count = base * (0.35 if weekend else 1.0) * rng.uniform(0.85, 1.15)
    if source_id == "nyc-tlc":
        count = base * (1 + 0.05 * ((day_index // 30) % 3))
    median = base * (0.35 if weekend else 1.0)
    learning = day_index < 7
    volume_status = Status.OK
    if not learning and (count < median * 0.5 or count > median * 2.0):
        volume_status = Status.DEGRADED
    outcomes = [
        o(
            "availability",
            Status.OK,
            f"HTTP 200 in {rng.randint(90, 900)} ms",
            severity=Severity.CRITICAL,
            metric=float(rng.randint(90, 900)),
            unit="ms",
        ),
    ]
    if source_id == "nyc-tlc":
        age = (day_index % 40) + 20
        fresh = Status.OK if age <= 45 else Status.DEGRADED if age <= 75 else Status.FAILED
        outcomes.append(
            o(
                "freshness",
                fresh,
                f"newest month, {age} days after month end (ok within 45)",
                severity=Severity.CRITICAL,
                metric=float(age),
                threshold=45.0,
                unit="days",
            )
        )
    elif source_id == "usgs-earthquakes":
        age_h = rng.uniform(0.1, 1.5)
        outcomes.append(
            o(
                "freshness",
                Status.OK,
                f"newest event {age_h:.1f} h ago (ok within 6 h)",
                severity=Severity.CRITICAL,
                metric=round(age_h, 2),
                threshold=6.0,
                unit="hours",
            )
        )
    else:
        age = 1 if rng.random() < 0.9 else 2
        outcomes.append(
            o(
                "freshness",
                Status.OK,
                f"newest record, {age} days behind (ok within 2)",
                severity=Severity.CRITICAL,
                metric=float(age),
                threshold=2.0,
                unit="days",
            )
        )
    details = (
        {"learning": True, "samples": day_index}
        if learning
        else {
            "median": median,
            "low": median * 0.5,
            "high": median * 2.0,
            "samples": min(day_index, 28),
            "weekday_matched": weekend,
        }
    )
    outcomes.append(
        o(
            "volume",
            volume_status,
            f"{count:,.0f} {'(learning)' if learning else 'within band'}",
            metric=round(count),
            threshold=None if learning else median,
            unit="count",
            details=details,
        )
    )
    fields = [{"name": "a", "dtype": "VARCHAR"}, {"name": "b", "dtype": "BIGINT"}]
    if day_index >= DRIFT_FROM.get(source_id, 10**6):
        fields = [*fields, {"name": "complaint_what_happened_redacted", "dtype": "VARCHAR"}]
        outcomes.append(
            o(
                "schema",
                Status.DEGRADED,
                "schema changed: +complaint_what_happened_redacted",
                metric=3.0,
                threshold=2.0,
                unit="fields",
                details={"diff": ["+complaint_what_happened_redacted"], "fields": fields},
            )
        )
    else:
        outcomes.append(
            o(
                "schema",
                Status.OK,
                "schema unchanged (2 fields)",
                metric=2.0,
                unit="fields",
                details={"fields": fields, "accepted": True},
            )
        )
    if source_id in ("cfpb-complaints", "nyc-311", "usgs-earthquakes"):
        outcomes.append(
            o(
                "values:sample_check",
                Status.OK,
                "0 of 200 rows outside the allowed set",
                metric=0.0,
                threshold=0.0,
                unit="share of sample rows",
            )
        )
    return outcomes


def main(out: Path = OUT) -> Path:
    rng = random.Random(42)
    if out.exists():
        shutil.rmtree(out)
    store = DataStore(out)
    sources = load_sources(SOURCES_DIR)
    book = store.read_incidents()
    previous: list[Outcome] = []
    first_day = RUN_AT - timedelta(days=DAYS - 1)
    for index in range(DAYS):
        if index in SKIPPED_DAYS:
            continue
        run_at = first_day + timedelta(days=index, minutes=rng.randint(-2, 9))
        run_id = f"fixture{index:03d}"
        outcomes = [
            oc.model_copy(update={"run_id": run_id})
            for s in sources
            for oc in _outcomes(rng, s.id, index, run_at)
        ]
        store.write_outcomes(outcomes)
        incidents.apply(book, outcomes, previous, run_at)
        previous = outcomes
        if index == DAYS - 1:
            by_source = {}
            for s in sources:
                mine = [o for o in outcomes if o.source_id == s.id]
                from sourcewatch.status import source_status

                by_source[s.id] = SourceSummary(status=source_status(mine), detail=mine[1].message)
            from sourcewatch.status import overall_line, overall_status

            statuses = {k: v.status for k, v in by_source.items()}
            store.write_last_run(
                LastRun(
                    run_id=run_id,
                    run_at=run_at,
                    overall=overall_status(statuses),
                    line=overall_line(statuses),
                    sources=by_source,
                )
            )
    store.write_incidents(book)
    return out


if __name__ == "__main__":
    print(main())
