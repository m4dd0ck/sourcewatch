"""Render the static status site and JSON feeds from the data directory."""

import json
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader, select_autoescape

from sourcewatch import __version__
from sourcewatch.charts import sparkline_svg, strip_svg
from sourcewatch.clock import Clock
from sourcewatch.config import SourceConfig, load_expected_schema, load_sources
from sourcewatch.errors import UnsafeOutputError
from sourcewatch.incidents import Incident
from sourcewatch.outcome import Outcome, check_sort_key
from sourcewatch.status import effective_status, overall_line, overall_status, source_status
from sourcewatch.store import DataStore
from sourcewatch.types import Status

SITE_MARKER = ".sourcewatch-site"
STRIP_DAYS = 90
EASTERN = ZoneInfo("America/New_York")

_env = Environment(
    loader=PackageLoader("sourcewatch", "templates"),
    autoescape=select_autoescape(["j2", "html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass
class SourcePage:
    source: SourceConfig
    status: Status
    detail: str
    latest: list[Outcome]
    strip: str
    volume_spark: str
    freshness_spark: str
    incidents: list[Incident]
    schema_fields: list[dict[str, str]]
    schema_accepted: bool
    schema_diff: list[str]
    day_statuses: list[tuple[date, Status | None]]
    last_seen: datetime | None

    @property
    def uptime_pct(self) -> float | None:
        observed = [s for _, s in self.day_statuses if s is not None]
        if not observed:
            return None
        return 100 * sum(1 for s in observed if s == Status.OK) / len(observed)


def _days(now: datetime, n: int) -> list[date]:
    today = now.astimezone(UTC).date()
    return [today - timedelta(days=i) for i in range(n - 1, -1, -1)]


def _status_by_day(outcomes: list[Outcome], days: list[date]) -> list[tuple[date, Status | None]]:
    by_day: dict[date, list[Outcome]] = defaultdict(list)
    for o in outcomes:
        by_day[o.run_at.astimezone(UTC).date()].append(o)
    return [(d, source_status(by_day[d]) if by_day.get(d) else None) for d in days]


def _latest_run(outcomes: list[Outcome]) -> list[Outcome]:
    if not outcomes:
        return []
    newest = max(o.run_at for o in outcomes)
    return sorted(
        (o for o in outcomes if o.run_at == newest), key=lambda o: check_sort_key(o.check)
    )


def _series(
    outcomes: list[Outcome], check: str, days: list[date]
) -> list[tuple[date, float | None]]:
    by_day: dict[date, float] = {}
    for o in sorted(outcomes, key=lambda o: o.run_at):
        if o.check == check and o.metric is not None:
            by_day[o.run_at.astimezone(UTC).date()] = o.metric
    return [(d, by_day.get(d)) for d in days]


def _band(latest: list[Outcome]) -> tuple[float, float] | None:
    vol = next((o for o in latest if o.check == "volume"), None)
    if vol and "low" in vol.details and "high" in vol.details:
        return float(vol.details["low"]), float(vol.details["high"])
    return None


def build_source_page(
    source: SourceConfig,
    outcomes: list[Outcome],
    incidents: list[Incident],
    sources_dir: Path,
    days: list[date],
) -> SourcePage:
    latest = _latest_run(outcomes)
    status = source_status(latest) if latest else Status.UNKNOWN
    day_statuses = _status_by_day(outcomes, days)
    schema_outcome = next((o for o in latest if o.check == "schema"), None)
    expected = load_expected_schema(sources_dir, source)
    fields = list(schema_outcome.details.get("fields", [])) if schema_outcome else []
    detail = ""
    for o in latest:
        if o.check == "availability" and o.status != Status.OK:
            detail = o.message
            break
    if not detail:
        fresh = next((o for o in latest if o.check == "freshness"), None)
        detail = fresh.message if fresh else "no observations yet"
    return SourcePage(
        source=source,
        status=status,
        detail=detail,
        latest=latest,
        strip=strip_svg(day_statuses, f"{source.name}: last {len(days)} days"),
        volume_spark=sparkline_svg(
            _series(outcomes, "volume", days), _band(latest), f"{source.volume.metric} over time"
        ),
        freshness_spark=sparkline_svg(
            _series(outcomes, "freshness", days), None, "freshness age over time"
        ),
        incidents=sorted(
            (i for i in incidents if i.source_id == source.id), key=lambda i: i.id, reverse=True
        ),
        schema_fields=fields,
        schema_accepted=expected is not None,
        schema_diff=list(schema_outcome.details.get("diff", [])) if schema_outcome else [],
        day_statuses=day_statuses,
        last_seen=latest[0].run_at if latest else None,
    )


def build_site(sources_dir: Path, data_dir: Path, out_dir: Path, clock: Clock) -> Path:
    """Render everything into ``out_dir`` and return the index path.

    Raises:
        UnsafeOutputError: if ``out_dir`` holds files sourcewatch did not write.
    """
    now = clock.now()
    sources = load_sources(sources_dir)
    store = DataStore(data_dir)
    history = store.read_history(STRIP_DAYS, now)
    book = store.read_incidents()
    last_run = store.read_last_run()
    days = _days(now, STRIP_DAYS)

    pages = [
        build_source_page(
            s, [o for o in history if o.source_id == s.id], book.incidents, sources_dir, days
        )
        for s in sources
    ]
    statuses = {p.source.id: p.status for p in pages}
    overall = overall_status(statuses)
    line = overall_line(statuses)
    incidents = sorted(book.incidents, key=lambda i: (i.is_open, i.id), reverse=True)

    _prepare(out_dir)
    (out_dir / "sources").mkdir()
    common = {
        "generated_at": now,
        "generated_at_et": now.astimezone(EASTERN),
        "last_run": last_run,
        "overall": overall,
        "line": line,
        "version": __version__,
        "open_count": len(book.open),
        "effective": effective_status,
    }
    _write(out_dir / "index.html", "index.html.j2", pages=pages, **common)
    for page in pages:
        _write(
            out_dir / "sources" / f"{page.source.id}.html", "source.html.j2", page=page, **common
        )
    _write(out_dir / "incidents.html", "incidents.html.j2", incidents=incidents, **common)
    _write(
        out_dir / "about.html",
        "about.html.j2",
        sources=sources,
        budget=sum(s.request_budget for s in sources),
        **common,
    )
    (out_dir / "status.json").write_text(
        json.dumps(_status_json(now, overall, line, pages), indent=1) + "\n", encoding="utf-8"
    )
    (out_dir / "history.json").write_text(
        json.dumps(_history_json(days, pages), indent=1) + "\n", encoding="utf-8"
    )
    (out_dir / SITE_MARKER).write_text("Built by sourcewatch site; safe to delete.\n")
    return out_dir / "index.html"


def _prepare(out_dir: Path) -> None:
    if out_dir.exists():
        if any(out_dir.iterdir()) and not (out_dir / SITE_MARKER).exists():
            raise UnsafeOutputError(f"{out_dir} is not empty and was not built by sourcewatch site")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)


def _write(path: Path, template: str, **context: Any) -> None:
    path.write_text(_env.get_template(template).render(**context), encoding="utf-8")


def _status_json(
    now: datetime, overall: Status, line: str, pages: list[SourcePage]
) -> dict[str, Any]:
    return {
        "generated_at": now.isoformat(),
        "overall": overall.value,
        "line": line,
        "sources": [
            {
                "id": p.source.id,
                "name": p.source.name,
                "status": p.status.value,
                "detail": p.detail,
                "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                "checks": [
                    {
                        "check": o.check,
                        "status": o.status.value,
                        "severity": o.severity.value,
                        "metric": o.metric,
                        "threshold": o.threshold,
                        "unit": o.unit,
                        "message": o.message,
                    }
                    for o in p.latest
                ],
                "open_incidents": [i.id for i in p.incidents if i.is_open],
            }
            for p in pages
        ],
    }


def _history_json(days: list[date], pages: list[SourcePage]) -> dict[str, Any]:
    return {
        "days": [d.isoformat() for d in days],
        "sources": {
            p.source.id: [s.value if s else None for _, s in p.day_statuses] for p in pages
        },
    }
