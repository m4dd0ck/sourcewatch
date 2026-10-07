"""Shared enums used by checks, store, status roll-up and the site."""

from enum import StrEnum


class Status(StrEnum):
    """Outcome of a check, a source, or a whole run."""

    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    """How much a failing check should count against its source."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# Reason: worst-first order for roll-ups; failed beats unknown because a confirmed failure
# is more actionable than a missing observation.
STATUS_RANK: dict[Status, int] = {
    Status.OK: 0,
    Status.DEGRADED: 1,
    Status.UNKNOWN: 2,
    Status.FAILED: 3,
}
