"""Command line: run, site, backfill, accept-schema, sources, validate-config."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from sourcewatch import __version__
from sourcewatch.clock import FixedClock, SystemClock
from sourcewatch.config import load_expected_schema, load_sources
from sourcewatch.errors import ConfigError, SourcewatchError
from sourcewatch.log import configure_logging

app = typer.Typer(
    help="A status page for public datasets.", no_args_is_help=True, add_completion=False
)

SourcesOpt = Annotated[Path, typer.Option("--sources", help="Directory of source YAML files")]
DataOpt = Annotated[Path, typer.Option("--data-dir", help="Where observations are kept")]
DEFAULT_SOURCES = Path("sources")
DEFAULT_DATA = Path("data")


def _fail(exc: SourcewatchError, code: int) -> None:
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(code)


@app.callback()
def _main(
    log_level: Annotated[str, typer.Option("--log-level", help="DEBUG, INFO, WARNING")] = "WARNING",
) -> None:
    configure_logging(log_level)


@app.command()
def run(
    sources: SourcesOpt = DEFAULT_SOURCES,
    data_dir: DataOpt = DEFAULT_DATA,
    source: Annotated[str | None, typer.Option("--source", help="Only this source id")] = None,
) -> None:
    """Probe every source, record the outcomes and print the status table."""
    from sourcewatch.runner import format_table, run_all

    try:
        report = run_all(sources, data_dir, SystemClock(), only=source)
    except ConfigError as exc:
        _fail(exc, 2)
        return
    typer.echo(format_table(report))


@app.command()
def site(
    out: Annotated[Path, typer.Option("--out", help="Output directory for the static site")],
    sources: SourcesOpt = DEFAULT_SOURCES,
    data_dir: DataOpt = DEFAULT_DATA,
    at: Annotated[
        str | None, typer.Option("--at", help="Render as of this ISO timestamp (for fixtures)")
    ] = None,
) -> None:
    """Render the status site and JSON feeds from the data directory."""
    from sourcewatch.site import build_site

    clock = FixedClock(datetime.fromisoformat(at).astimezone(UTC)) if at else SystemClock()
    try:
        index = build_site(sources, data_dir, out, clock)
    except SourcewatchError as exc:
        _fail(exc, 2)
        return
    typer.echo(f"wrote {index}")


@app.command()
def backfill(
    sources: SourcesOpt = DEFAULT_SOURCES,
    data_dir: DataOpt = DEFAULT_DATA,
    source: Annotated[str | None, typer.Option("--source", help="Only this source id")] = None,
    days: Annotated[int, typer.Option("--days", help="How far back to ask each source")] = 56,
) -> None:
    """Fill volume baselines from each source's own history where it exposes one."""
    from sourcewatch.backfill import backfill_all

    try:
        results = backfill_all(sources, data_dir, SystemClock(), only=source, days=days)
    except ConfigError as exc:
        _fail(exc, 2)
        return
    for result in results:
        typer.echo(f"{result.source_id:<22} {result.message}")


@app.command("accept-schema")
def accept_schema(
    source_id: Annotated[str, typer.Argument(help="Source id whose current schema to accept")],
    sources: SourcesOpt = DEFAULT_SOURCES,
    data_dir: DataOpt = DEFAULT_DATA,
) -> None:
    """Write sources/<id>.schema.json from the schema seen on the most recent run."""
    from sourcewatch.backfill import accept_schema_from_history

    try:
        path = accept_schema_from_history(sources, data_dir, source_id, SystemClock())
    except SourcewatchError as exc:
        _fail(exc, 2)
        return
    typer.echo(f"accepted schema written to {path}; commit it to make the drift check green")


@app.command("sources")
def list_sources(sources: SourcesOpt = DEFAULT_SOURCES) -> None:
    """List the monitored sources."""
    try:
        loaded = load_sources(sources)
    except ConfigError as exc:
        _fail(exc, 2)
        return
    for s in loaded:
        typer.echo(f"{s.id:<22} {s.cadence.kind:<14} {s.probe.kind:<15} {s.name}")


@app.command("validate-config")
def validate_config(sources: SourcesOpt = DEFAULT_SOURCES) -> None:
    """Check every source definition and accepted schema file."""
    try:
        loaded = load_sources(sources)
        accepted = sum(1 for s in loaded if load_expected_schema(sources, s) is not None)
    except ConfigError as exc:
        _fail(exc, 2)
        return
    typer.echo(f"{len(loaded)} sources valid, {accepted} with an accepted schema")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)
