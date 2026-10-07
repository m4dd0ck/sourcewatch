"""Roll outcomes up to a source status and a one-line overall summary."""

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from sourcewatch.outcome import Outcome
from sourcewatch.types import STATUS_RANK, Severity, Status

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def effective_status(outcome: Outcome) -> Status:
    """A failed check of warning or info severity only degrades its source."""
    if outcome.status == Status.FAILED and outcome.severity != Severity.CRITICAL:
        return Status.DEGRADED
    return outcome.status


def source_status(outcomes: Iterable[Outcome]) -> Status:
    worst = Status.OK
    for outcome in outcomes:
        status = effective_status(outcome)
        if STATUS_RANK[status] > STATUS_RANK[worst]:
            worst = status
    return worst


def overall_status(statuses: Mapping[str, Status]) -> Status:
    return source_status(
        Outcome(
            run_at=_EPOCH, source_id=k, check="x", status=v, severity=Severity.CRITICAL, message=""
        )
        for k, v in statuses.items()
    )


def overall_line(statuses: Mapping[str, Status]) -> str:
    """ "All 6 sources healthy" or "1 source degraded, 1 failed"."""
    total = len(statuses)
    counts = {s: sum(1 for v in statuses.values() if v == s) for s in Status}
    if counts[Status.OK] == total:
        return f"All {total} sources healthy"
    parts = []
    for status in (Status.FAILED, Status.UNKNOWN, Status.DEGRADED):
        n = counts[status]
        if n:
            parts.append(f"{n} source{'s' if n != 1 else ''} {status.value}")
    return ", ".join(parts)
