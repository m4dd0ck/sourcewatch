"""One run: probe every source, score it, persist the outcomes, update incidents, print a table."""

import os
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import httpx
import structlog

from sourcewatch import checks
from sourcewatch.clock import Clock
from sourcewatch.config import SourceConfig, load_expected_schema, load_sources
from sourcewatch.errors import ConfigError, ProbeError
from sourcewatch.http import Sleeper, make_client
from sourcewatch.incidents import Incident
from sourcewatch.incidents import apply as apply_incidents
from sourcewatch.observation import Observation
from sourcewatch.outcome import Outcome, check_sort_key
from sourcewatch.probes import PROBES, ProbeContext
from sourcewatch.status import effective_status, overall_line, overall_status, source_status
from sourcewatch.store import DataStore, LastRun, SourceSummary
from sourcewatch.table_checks import run_table_checks
from sourcewatch.types import Status

log = structlog.get_logger()


@dataclass
class SourceReport:
    source: SourceConfig
    observation: Observation
    outcomes: list[Outcome]

    @property
    def status(self) -> Status:
        return source_status(self.outcomes)

    @property
    def detail(self) -> str:
        return self.observation.notes or self.observation.unavailable_reason

    def outcome(self, check: str) -> Outcome | None:
        return next((o for o in self.outcomes if o.check == check), None)

    @property
    def value_outcomes(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.check.startswith("values:")]


@dataclass
class RunReport:
    run_id: str
    run_at: str
    reports: list[SourceReport]
    opened: list[Incident] = field(default_factory=list)
    resolved: list[Incident] = field(default_factory=list)
    open_incidents: int = 0
    written: Path | None = None
    seconds: float = 0.0

    @property
    def statuses(self) -> dict[str, Status]:
        return {r.source.id: r.status for r in self.reports}

    @property
    def overall(self) -> Status:
        return overall_status(self.statuses)

    @property
    def line(self) -> str:
        return overall_line(self.statuses)

    @property
    def outcomes(self) -> list[Outcome]:
        return [o for r in self.reports for o in r.outcomes]


def observe(
    source: SourceConfig,
    ctx: ProbeContext,
    store: DataStore,
    sources_dir: Path,
    workdir: Path,
) -> SourceReport:
    """Probe one source and run every check on what came back."""
    now = ctx.now()
    log.info("probe.start", source=source.id, kind=source.probe.kind)
    try:
        obs = PROBES[source.probe.kind](source, ctx)
    except ProbeError as exc:
        obs = Observation(
            source_id=source.id,
            observed_at=now,
            available=False,
            unavailable_reason=str(exc),
            http=exc.detail,
        )
    log.info(
        "probe.done",
        source=source.id,
        available=obs.available,
        requests=ctx.requests_made,
        notes=obs.notes or obs.unavailable_reason,
    )

    outcomes = [checks.availability(source, obs)]
    if obs.available:
        outcomes.append(checks.freshness(source, obs, now))
        outcomes.append(checks.volume(source, obs, store.volume_samples(source.id, now), now))
        outcomes.append(checks.schema(source, obs, load_expected_schema(sources_dir, source)))
        outcomes.extend(run_table_checks(source, obs, workdir))
    else:
        outcomes.extend(
            checks.unobserved(source, obs, c) for c in ("freshness", "volume", "schema")
        )
    outcomes.sort(key=lambda o: check_sort_key(o.check))
    for outcome in outcomes:
        log.info(
            "check.outcome",
            source=source.id,
            check=outcome.check,
            status=outcome.status.value,
            message=outcome.message,
        )
    return SourceReport(source, obs, outcomes)


def run_all(
    sources_dir: Path,
    data_dir: Path,
    clock: Clock,
    *,
    only: str | None = None,
    client: httpx.Client | None = None,
    env: Mapping[str, str] | None = None,
    sleep: Sleeper = time.sleep,
) -> RunReport:
    """Run every source (or ``only`` one) and persist the results under ``data_dir``.

    Raises:
        ConfigError: if the source definitions are invalid or ``only`` names an unknown source.
    """
    started = time.monotonic()
    sources = load_sources(sources_dir)
    if only is not None:
        sources = [s for s in sources if s.id == only]
        if not sources:
            raise ConfigError(f"no source with id {only!r} in {sources_dir}")
    store = DataStore(data_dir)
    run_id = uuid4().hex
    run_at = clock.now()
    previous = store.read_previous_run(run_id, run_at)
    own_client = client is None
    http_client = client or make_client()
    reports: list[SourceReport] = []
    try:
        with tempfile.TemporaryDirectory(prefix="sourcewatch-") as tmp:
            workdir = Path(tmp)
            for source in sources:
                ctx = ProbeContext(
                    client=http_client,
                    clock=clock,
                    workdir=workdir / source.id,
                    budget=source.request_budget,
                    env=dict(env) if env is not None else dict(os.environ),
                    sleep=sleep,
                )
                reports.append(observe(source, ctx, store, sources_dir, workdir / source.id))
    finally:
        if own_client:
            http_client.close()

    outcomes = [o.model_copy(update={"run_id": run_id}) for r in reports for o in r.outcomes]
    for source_report, chunk in zip(reports, _chunks(outcomes, reports), strict=True):
        source_report.outcomes = chunk
    written = store.write_outcomes(outcomes)

    book = store.read_incidents()
    opened, resolved = apply_incidents(book, outcomes, previous, run_at)
    store.write_incidents(book)
    for incident in opened:
        log.warning(
            "incident.opened", id=incident.id, source=incident.source_id, check=incident.check
        )
    for incident in resolved:
        log.info(
            "incident.resolved", id=incident.id, source=incident.source_id, check=incident.check
        )

    report = RunReport(
        run_id=run_id,
        run_at=run_at.isoformat(),
        reports=reports,
        opened=opened,
        resolved=resolved,
        open_incidents=len(book.open),
        written=written,
        seconds=time.monotonic() - started,
    )
    store.write_last_run(
        LastRun(
            run_id=run_id,
            run_at=run_at,
            overall=report.overall,
            line=report.line,
            sources={r.source.id: SourceSummary(status=r.status, detail=r.detail) for r in reports},
        )
    )
    log.info(
        "store.written", path=str(written), outcomes=len(outcomes), overall=report.overall.value
    )
    return report


def _chunks(outcomes: list[Outcome], reports: list[SourceReport]) -> list[list[Outcome]]:
    chunks: list[list[Outcome]] = []
    index = 0
    for report in reports:
        n = len(report.outcomes)
        chunks.append(outcomes[index : index + n])
        index += n
    return chunks


_CELL = {Status.OK: "ok", Status.DEGRADED: "warn", Status.FAILED: "FAIL", Status.UNKNOWN: "-"}


def _cell(outcome: Outcome | None) -> str:
    if outcome is None:
        return "-"
    if outcome.check == "schema" and outcome.status in (Status.DEGRADED, Status.FAILED):
        return "DRIFT"
    return _CELL[effective_status(outcome)]


def _values_cell(report: SourceReport) -> str:
    values = report.value_outcomes
    if not values:
        return "-"
    worst = source_status(values)
    return _CELL[worst]


def format_table(report: RunReport) -> str:
    """The console table printed after a run (see INITIAL Example 1)."""
    header = (
        f"{'source':<22} {'avail':<6} {'fresh':<6} {'volume':<7} {'schema':<7} "
        f"{'values':<7} {'status':<9} detail"
    )
    lines = [f"sourcewatch run {report.run_at}", "", header]
    for r in report.reports:
        cells = [
            _cell(r.outcome("availability")),
            _cell(r.outcome("freshness")),
            _cell(r.outcome("volume")),
            _cell(r.outcome("schema")),
            _values_cell(r),
        ]
        lines.append(
            f"{r.source.id:<22} {cells[0]:<6} {cells[1]:<6} {cells[2]:<7} {cells[3]:<7} "
            f"{cells[4]:<7} {r.status.value:<9} {r.detail}"
        )
    checks_run = len(report.outcomes)
    failed = sum(1 for o in report.outcomes if effective_status(o) == Status.FAILED)
    lines.append("")
    lines.append(
        f"{len(report.reports)} sources, {checks_run} checks, {failed} failed, "
        f"{report.open_incidents} incidents open. {report.seconds:.1f} s. {report.line}."
    )
    if report.opened:
        lines.append(
            "opened: " + ", ".join(f"{i.source_id} #{i.id} ({i.check})" for i in report.opened)
        )
    if report.resolved:
        lines.append(
            "resolved: " + ", ".join(f"{i.source_id} #{i.id} ({i.check})" for i in report.resolved)
        )
    if report.written:
        lines.append(f"wrote {report.written}")
    return "\n".join(lines)
