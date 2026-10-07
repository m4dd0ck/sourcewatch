"""Incidents: a failing observation that repeats becomes an incident until the source recovers."""

from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from sourcewatch.outcome import Outcome
from sourcewatch.types import Status


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    source_id: str
    check: str
    opened_at: datetime
    resolved_at: datetime | None = None
    first_message: str
    last_message: str
    runs: int = 1

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None

    @property
    def key(self) -> tuple[str, str]:
        return self.source_id, self.check


class IncidentBook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_id: int = 1
    incidents: list[Incident] = Field(default_factory=list)

    def open_for(self, key: tuple[str, str]) -> Incident | None:
        for incident in self.incidents:
            if incident.is_open and incident.key == key:
                return incident
        return None

    @property
    def open(self) -> list[Incident]:
        return [i for i in self.incidents if i.is_open]


def _counts_as_failing(outcome: Outcome) -> bool:
    # Reason: schema drift is reported as degraded but is an incident from the first run,
    # because nothing will fix it except a human accepting the change.
    if outcome.check == "schema":
        return outcome.status in (Status.DEGRADED, Status.FAILED)
    # Reason: when a source is down its other checks are "unknown"; the availability incident
    # already says so, and one outage should not open four incidents.
    if outcome.status == Status.UNKNOWN:
        return outcome.check == "availability"
    return outcome.status == Status.FAILED


def _opens_immediately(outcome: Outcome) -> bool:
    return outcome.check == "schema" or (
        outcome.check == "freshness" and outcome.status == Status.FAILED
    )


def apply(
    book: IncidentBook,
    outcomes: Iterable[Outcome],
    previous: Iterable[Outcome],
    run_at: datetime,
) -> tuple[list[Incident], list[Incident]]:
    """Update ``book`` in place from this run's outcomes; return (opened, resolved).

    Args:
        book: The incident book to update.
        outcomes: This run's outcomes.
        previous: The previous run's outcomes, used for the "two consecutive runs" rule.
        run_at: Timestamp for anything opened or resolved now.
    """
    previously_failing = {(o.source_id, o.check) for o in previous if _counts_as_failing(o)}
    opened: list[Incident] = []
    resolved: list[Incident] = []
    seen: set[tuple[str, str]] = set()

    for outcome in outcomes:
        key = (outcome.source_id, outcome.check)
        seen.add(key)
        current = book.open_for(key)
        if _counts_as_failing(outcome):
            if current is not None:
                current.runs += 1
                current.last_message = outcome.message
            elif key in previously_failing or _opens_immediately(outcome):
                incident = Incident(
                    id=book.next_id,
                    source_id=outcome.source_id,
                    check=outcome.check,
                    opened_at=run_at,
                    first_message=outcome.message,
                    last_message=outcome.message,
                )
                book.next_id += 1
                book.incidents.append(incident)
                opened.append(incident)
        elif current is not None:
            current.resolved_at = run_at
            current.last_message = f"resolved: {outcome.message}"
            resolved.append(current)
    return opened, resolved
