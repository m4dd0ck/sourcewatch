"""The data directory: Parquet day files of outcomes plus small JSON/Parquet state files.

Layout (an orphan ``data`` branch in production, a temp dir in tests)::

    data/
    ├── history/YYYY/MM/DD.parquet   one row per Outcome; same-day runs merged on run_id
    └── state/
        ├── incidents.json
        ├── baselines.parquet          source_id, metric, day, value, origin
        └── last_run.json
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from sourcewatch.baselines import Sample
from sourcewatch.errors import StoreError
from sourcewatch.incidents import IncidentBook
from sourcewatch.outcome import Outcome
from sourcewatch.types import Status

OUTCOME_COLUMNS = {
    "run_id": "VARCHAR",
    "run_at": "TIMESTAMP",
    "source_id": "VARCHAR",
    "check": "VARCHAR",
    "status": "VARCHAR",
    "severity": "VARCHAR",
    "metric": "DOUBLE",
    "threshold": "DOUBLE",
    "unit": "VARCHAR",
    "message": "VARCHAR",
    "details": "VARCHAR",
}
BASELINE_COLUMNS = {
    "source_id": "VARCHAR",
    "metric": "VARCHAR",
    "day": "DATE",
    "value": "DOUBLE",
    "origin": "VARCHAR",
}


class BaselineRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    metric: str
    day: date
    value: float
    origin: str = "run"


class SourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status
    detail: str


class LastRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    run_at: datetime
    overall: Status
    line: str
    sources: dict[str, SourceSummary] = Field(default_factory=dict)


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    return con


def _quote(literal: str) -> str:
    return "'" + literal.replace("'", "''") + "'"


def _select(columns: dict[str, str]) -> str:
    # Reason: "check" is a reserved word in DuckDB, so every column name is quoted.
    return ", ".join(f'"{name}"' for name in columns)


def _columns_sql(columns: dict[str, str]) -> str:
    return "{" + ", ".join(f"{_quote(k)}: {_quote(v)}" for k, v in columns.items()) + "}"


def _write_parquet(rows: list[dict[str, Any]], columns: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json")
    tmp.write_text(json.dumps(rows, default=str), encoding="utf-8")
    con = _connect()
    try:
        # Reason: column lists are module constants and COPY ... TO cannot bind its target path.
        con.execute(
            f"COPY (SELECT * FROM read_json($src::VARCHAR, format='array', "  # nosec B608
            f"columns={_columns_sql(columns)})) TO {_quote(str(path))} (FORMAT PARQUET)",
            {"src": str(tmp)},
        )
    except duckdb.Error as exc:
        raise StoreError(f"could not write {path}: {exc}") from exc
    finally:
        con.close()
        tmp.unlink(missing_ok=True)


def _outcome_row(outcome: Outcome) -> dict[str, Any]:
    row = outcome.model_dump(mode="json")
    # Reason: stored as a naive UTC TIMESTAMP; TIMESTAMPTZ would make DuckDB import pytz on read.
    row["run_at"] = outcome.run_at.astimezone(UTC).replace(tzinfo=None).isoformat()
    row["details"] = json.dumps(outcome.details, sort_keys=True, default=str)
    return row


def _row_outcome(row: tuple[Any, ...]) -> Outcome:
    data = dict(zip(OUTCOME_COLUMNS, row, strict=True))
    data["details"] = json.loads(data["details"]) if data["details"] else {}
    data["run_at"] = data["run_at"].replace(tzinfo=UTC)
    return Outcome.model_validate(data)


class DataStore:
    """Reads and writes everything under one data directory."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.history_dir = data_dir / "history"
        self.state_dir = data_dir / "state"

    # history -----------------------------------------------------------------------------

    def day_file(self, day: date) -> Path:
        return self.history_dir / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}.parquet"

    def write_outcomes(self, outcomes: list[Outcome]) -> Path:
        """Write a run's outcomes into its UTC day file, keeping any earlier run that day."""
        if not outcomes:
            raise StoreError("nothing to write")
        day = outcomes[0].run_at.astimezone(UTC).date()
        path = self.day_file(day)
        run_ids = {o.run_id for o in outcomes}
        earlier = [o for o in self._read_files([path]) if o.run_id not in run_ids]
        _write_parquet([_outcome_row(o) for o in [*earlier, *outcomes]], OUTCOME_COLUMNS, path)
        return path

    def history_files(self) -> list[Path]:
        return sorted(self.history_dir.glob("*/*/*.parquet"))

    def read_history(self, days: int, now: datetime) -> list[Outcome]:
        """Every outcome from the last ``days`` UTC days up to ``now``."""
        cutoff = now.astimezone(UTC)
        since = (cutoff - timedelta(days=days)).date()
        files = [p for p in self.history_files() if since <= _day_of(p) <= cutoff.date()]
        return [o for o in self._read_files(files) if o.run_at <= cutoff]

    def read_previous_run(self, before_run_id: str, now: datetime) -> list[Outcome]:
        """Outcomes of the most recent run other than ``before_run_id``, within 14 days."""
        recent = [o for o in self.read_history(14, now) if o.run_id != before_run_id]
        if not recent:
            return []
        latest = max(o.run_at for o in recent)
        return [o for o in recent if o.run_at == latest]

    def volume_samples(self, source_id: str, now: datetime, days: int = 400) -> list[Sample]:
        """Volume values from history plus backfilled baselines, one per day (history wins)."""
        by_day: dict[date, float] = {}
        for row in self.read_baselines():
            if row.source_id == source_id:
                by_day[row.day] = row.value
        for o in self.read_history(days, now):
            if o.source_id == source_id and o.check == "volume" and o.metric is not None:
                by_day[o.run_at.astimezone(UTC).date()] = o.metric
        return [Sample(day, value) for day, value in sorted(by_day.items())]

    def _read_files(self, files: list[Path]) -> list[Outcome]:
        existing = [str(p) for p in files if p.exists()]
        if not existing:
            return []
        con = _connect()
        try:
            rows = con.execute(
                f"SELECT {_select(OUTCOME_COLUMNS)} "  # nosec B608 - module-constant column list
                "FROM read_parquet($files, union_by_name=true) "
                'ORDER BY run_at, source_id, "check"',
                {"files": existing},
            ).fetchall()
        except duckdb.Error as exc:
            raise StoreError(f"could not read history: {exc}") from exc
        finally:
            con.close()
        return [_row_outcome(r) for r in rows]

    # state -------------------------------------------------------------------------------

    def read_incidents(self) -> IncidentBook:
        path = self.state_dir / "incidents.json"
        if not path.exists():
            return IncidentBook()
        return IncidentBook.model_validate_json(path.read_text(encoding="utf-8"))

    def write_incidents(self, book: IncidentBook) -> Path:
        path = self.state_dir / "incidents.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(book.model_dump_json(indent=1) + "\n", encoding="utf-8")
        return path

    def read_baselines(self) -> list[BaselineRow]:
        path = self.state_dir / "baselines.parquet"
        if not path.exists():
            return []
        con = _connect()
        try:
            rows = con.execute(
                f"SELECT {_select(BASELINE_COLUMNS)} FROM read_parquet($p::VARCHAR) "  # nosec B608
                "ORDER BY source_id, day",
                {"p": str(path)},
            ).fetchall()
        except duckdb.Error as exc:
            raise StoreError(f"could not read baselines: {exc}") from exc
        finally:
            con.close()
        return [BaselineRow(**dict(zip(BASELINE_COLUMNS, r, strict=True))) for r in rows]

    def write_baselines(self, rows: list[BaselineRow]) -> Path:
        path = self.state_dir / "baselines.parquet"
        _write_parquet([r.model_dump(mode="json") for r in rows], BASELINE_COLUMNS, path)
        return path

    def read_last_run(self) -> LastRun | None:
        path = self.state_dir / "last_run.json"
        if not path.exists():
            return None
        return LastRun.model_validate_json(path.read_text(encoding="utf-8"))

    def write_last_run(self, last: LastRun) -> Path:
        path = self.state_dir / "last_run.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(last.model_dump_json(indent=1) + "\n", encoding="utf-8")
        return path


def _day_of(path: Path) -> date:
    return date(int(path.parent.parent.name), int(path.parent.name), int(path.stem))
