"""One row of history: what one check concluded about one source on one run."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sourcewatch.types import Severity, Status

CHECK_ORDER = ("availability", "freshness", "volume", "schema")


class Outcome(BaseModel):
    """The result of a single check. ``run_id`` is stamped by the runner."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = ""
    run_at: datetime
    source_id: str
    check: str
    status: Status
    severity: Severity = Severity.WARNING
    metric: float | None = None
    threshold: float | None = None
    unit: str = ""
    message: str
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_failing(self) -> bool:
        return self.status in (Status.FAILED, Status.UNKNOWN)


def check_sort_key(check: str) -> tuple[int, str]:
    """Native checks first in a fixed order, then observatory value checks alphabetically."""
    try:
        return CHECK_ORDER.index(check), check
    except ValueError:
        return len(CHECK_ORDER), check
