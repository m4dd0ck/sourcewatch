"""Run observatory's table checks on a source's sample and translate the results."""

from pathlib import Path

from observatory import CheckConfig, CheckResult, CheckSuiteConfig, Observatory
from observatory import SourceConfig as ObservatorySource

from sourcewatch.config import SourceConfig
from sourcewatch.observation import Observation
from sourcewatch.outcome import Outcome
from sourcewatch.types import Severity, Status


def _status(result: CheckResult) -> Status:
    if result.status == "passed":
        return Status.OK
    if result.status == "error":
        return Status.UNKNOWN
    if result.status == "failed" and result.severity == "critical":
        return Status.FAILED
    return Status.DEGRADED


def run_table_checks(source: SourceConfig, obs: Observation, workdir: Path) -> list[Outcome]:
    """Observatory value checks over the probe sample; empty when there is nothing to do."""
    if not source.table_checks or obs.sample_parquet is None:
        return []
    suite = CheckSuiteConfig(
        name=f"{source.id}-sample",
        source=ObservatorySource(type="parquet", path=obs.sample_parquet),
        checks=[CheckConfig(**check) for check in source.table_checks],
    )
    # Reason: observatory always persists runs to a DuckDB file; we keep our own history, so the
    # file goes in the run's scratch directory and is discarded with it.
    result = Observatory(results_db=workdir / "observatory.db").run(suite)
    return [
        Outcome(
            run_at=obs.observed_at,
            source_id=source.id,
            check=f"values:{r.check_name}",
            status=_status(r),
            severity=Severity(r.severity),
            metric=float(r.metric_value) if r.metric_value is not None else None,
            threshold=float(r.threshold) if r.threshold is not None else None,
            unit="share of sample rows",
            message=r.message,
            details={"check_type": r.check_type, "sample_failures": r.sample_failures[:3]},
        )
        for r in result.check_results
    ]
