"""What every probe gets: a budgeted HTTP context, a clock, and helpers to write a sample."""

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import duckdb
import httpx

from sourcewatch.clock import Clock
from sourcewatch.config import SourceConfig
from sourcewatch.errors import ProbeError
from sourcewatch.http import Fetched, Sleeper, get_json, head
from sourcewatch.observation import Observation, SchemaField


@dataclass
class ProbeContext:
    """Everything a probe may touch. Counts requests so a probe cannot exceed its budget."""

    client: httpx.Client
    clock: Clock
    workdir: Path
    budget: int
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    sleep: Sleeper = time.sleep
    requests_made: int = 0
    last_fetched: Fetched | None = None

    def _spend(self, url: str) -> None:
        if self.requests_made >= self.budget:
            raise ProbeError(f"request budget of {self.budget} exhausted before {url}")
        self.requests_made += 1

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Budgeted GET returning parsed JSON; raises ProbeError on any failure."""
        self._spend(url)
        body, fetched = get_json(self.client, url, params=params, headers=headers, sleep=self.sleep)
        self.last_fetched = fetched
        return body

    def head(self, url: str) -> Fetched:
        """Budgeted HEAD; a 403/404 is an answer, not a failure."""
        self._spend(url)
        fetched = head(self.client, url, sleep=self.sleep)
        self.last_fetched = fetched
        return fetched

    def now(self) -> datetime:
        return self.clock.now()


class Probe(Protocol):
    """A probe turns one source definition into one Observation."""

    def __call__(self, source: SourceConfig, ctx: ProbeContext) -> Observation: ...


def write_sample(rows: list[dict[str, Any]], path: Path) -> Path:
    """Write a list of flat JSON objects as a Parquet file for observatory's table checks.

    DuckDB infers the column types from the JSON; nested values are kept as JSON text.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_json = path.with_suffix(".json")
    flat = [{k: _flatten_value(v) for k, v in row.items()} for row in rows]
    tmp_json.write_text(json.dumps(flat), encoding="utf-8")
    con = duckdb.connect()
    try:
        # Reason: COPY ... TO cannot take a bound parameter; the path is ours and quote-escaped.
        con.execute(
            "COPY (SELECT * FROM read_json_auto($src::VARCHAR, format='array')) "  # nosec B608
            f"TO {_quote(str(path))} (FORMAT PARQUET)",
            {"src": str(tmp_json)},
        )
    finally:
        con.close()
        tmp_json.unlink(missing_ok=True)
    return path


NULL_TYPE = "null"


def json_fields(rows: list[dict[str, Any]]) -> list[SchemaField]:
    """Field names and coarse JSON types seen across ``rows``, in first-seen order.

    Reason: inferring SQL types from a small sample flips between days (an all-null column, an
    integer that happens to have no decimals), which would read as schema drift. Coarse classes
    (string, number, boolean, object, array) are stable; a field that was null in every row is
    typed ``null`` and the drift check treats that as unknown rather than changed.
    """
    seen: dict[str, set[str]] = {}
    for row in rows:
        for key, value in row.items():
            kinds = seen.setdefault(key, set())
            kind = _json_kind(value)
            if kind != NULL_TYPE:
                kinds.add(kind)
    fields = []
    for name, kinds in seen.items():
        if not kinds:
            dtype = NULL_TYPE
        elif kinds == {"number"} or len(kinds) == 1:
            dtype = next(iter(kinds))
        else:
            dtype = "mixed"
        fields.append(SchemaField(name=name, dtype=dtype))
    return fields


def _json_kind(value: Any) -> str:
    if value is None:
        return NULL_TYPE
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def _flatten_value(value: Any) -> Any:
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True)
    return value


def _quote(literal: str) -> str:
    # Reason: COPY ... TO does not accept a bound parameter for its target path.
    return "'" + literal.replace("'", "''") + "'"
