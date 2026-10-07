from pathlib import Path

import httpx
import pytest

from sourcewatch.clock import FixedClock
from sourcewatch.probes.base import ProbeContext
from tests.conftest import no_sleep


@pytest.fixture
def ctx(client: httpx.Client, clock: FixedClock, tmp_path: Path) -> ProbeContext:
    return ProbeContext(
        client=client, clock=clock, workdir=tmp_path / "work", budget=6, env={}, sleep=no_sleep
    )
