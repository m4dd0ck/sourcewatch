"""Shared fixtures: a frozen clock, a temp data dir, the real sources dir and recorded responses."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from sourcewatch.clock import FixedClock
from sourcewatch.config import SourceConfig, load_sources

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "sources"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RESPONSES = FIXTURES / "responses"

# Wednesday 2026-10-07 11:03 UTC, the hour the daily workflow runs.
RUN_AT = datetime(2026, 10, 7, 11, 3, 41, tzinfo=UTC)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(RUN_AT)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def sources() -> list[SourceConfig]:
    return load_sources(SOURCES_DIR)


@pytest.fixture
def source_by_id(sources: list[SourceConfig]) -> dict[str, SourceConfig]:
    return {s.id: s for s in sources}


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=5.0) as c:
        yield c


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    # Reason: assert_all_mocked makes any unmocked request fail the test, so no test can reach
    # the internet by accident.
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def no_sleep(_seconds: float) -> None:
    """Replaces time.sleep inside retry loops."""


def load_response(name: str) -> Any:
    """A recorded JSON response from tests/fixtures/responses/<name>."""
    return json.loads((RESPONSES / name).read_text(encoding="utf-8"))
