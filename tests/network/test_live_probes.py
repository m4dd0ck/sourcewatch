"""Live smoke tests. Skipped by default; run with ``uv run pytest -m network``."""

from pathlib import Path

import httpx
import pytest

from sourcewatch.clock import SystemClock
from sourcewatch.config import SourceConfig
from sourcewatch.http import make_client
from sourcewatch.probes import PROBES
from sourcewatch.probes.base import ProbeContext

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def live_client() -> httpx.Client:
    with make_client() as client:
        yield client


@pytest.mark.parametrize(
    "source_id",
    [
        "cfpb-complaints",
        "nyc-311",
        "nyc-tlc",
        "open-meteo-archive",
        "treasury-dts",
        "usgs-earthquakes",
    ],
)
def test_each_probe_against_the_real_source(
    source_id: str, source_by_id: dict[str, SourceConfig], live_client: httpx.Client, tmp_path: Path
):
    source = source_by_id[source_id]
    ctx = ProbeContext(
        client=live_client,
        clock=SystemClock(),
        workdir=tmp_path,
        budget=source.request_budget,
        env={},
    )
    obs = PROBES[source.probe.kind](source, ctx)
    assert obs.available, obs.unavailable_reason
    assert ctx.requests_made <= source.request_budget
    assert obs.latest_event_at is not None
    assert obs.fields, "a live source should report a schema"
    assert obs.notes
