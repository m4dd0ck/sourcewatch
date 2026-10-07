"""What a probe returns: everything the checks need, and nothing from the source's rows."""

from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class HttpDetail(BaseModel):
    """The HTTP side of an observation, kept for the site and for incident messages."""

    model_config = ConfigDict(extra="forbid")

    url: str
    status: int | None = None
    latency_ms: int = 0
    attempts: int = 1
    error: str | None = None


class SchemaField(BaseModel):
    """One column or field of a source, by name and reported type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    dtype: str


class Observation(BaseModel):
    """A single look at one source.

    Attributes:
        source_id: The source's slug.
        observed_at: When the probe ran (UTC).
        available: Whether the source answered usefully.
        unavailable_reason: Why not, when ``available`` is False.
        http: Detail of the request that decided availability.
        latest_event_at: Newest event or record timestamp the source reports (UTC).
        latest_period: For monthly sources, the last day of the newest period available.
        published_at: When the source says it last updated, if it says.
        count: The volume figure for this run (rows, events, requests).
        count_label: What ``count`` counts, for messages and the site.
        fields: The source's current schema as the probe saw it.
        sample_parquet: A small sample written for observatory's table checks.
        notes: One line for the status table, e.g. "yellow 2026-08 published 2026-10-01".
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    observed_at: datetime
    available: bool
    unavailable_reason: str = ""
    http: HttpDetail | None = None
    latest_event_at: datetime | None = None
    latest_period: date | None = None
    published_at: datetime | None = None
    count: float | None = None
    count_label: str = "count"
    fields: list[SchemaField] | None = None
    sample_parquet: Path | None = None
    notes: str = ""
