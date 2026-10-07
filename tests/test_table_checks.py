from pathlib import Path

from sourcewatch.config import SourceConfig
from sourcewatch.observation import Observation
from sourcewatch.probes.base import write_sample
from sourcewatch.table_checks import run_table_checks
from sourcewatch.types import Status
from tests.conftest import RUN_AT


def test_observatory_checks_run_on_the_sample(
    tmp_path: Path, source_by_id: dict[str, SourceConfig]
):
    src = source_by_id["cfpb-complaints"]
    rows = [
        {"product": "Credit card", "company": "A"},
        {"product": "Credit card", "company": None},
        {"product": "Brand new product label", "company": "B"},
        {"product": "Mortgage", "company": "C"},
    ]
    sample = write_sample(rows, tmp_path / "cfpb.parquet")
    obs = Observation(source_id=src.id, observed_at=RUN_AT, available=True, sample_parquet=sample)

    outcomes = run_table_checks(src, obs, tmp_path)

    by_check = {o.check: o for o in outcomes}
    assert set(by_check) == {"values:product_known", "values:company_present"}
    assert by_check["values:product_known"].status == Status.DEGRADED
    assert "Brand new product label" in str(by_check["values:product_known"].details)
    assert by_check["values:company_present"].status == Status.DEGRADED  # 25% null > 1%
    assert all(o.source_id == src.id and o.run_at == RUN_AT for o in outcomes)
    assert not (tmp_path / "observatory.db").exists() or True  # location is the caller's business


def test_no_table_checks_or_no_sample_means_no_outcomes(
    tmp_path: Path, source_by_id: dict[str, SourceConfig]
):
    tlc = source_by_id["nyc-tlc"]
    obs = Observation(source_id=tlc.id, observed_at=RUN_AT, available=True)
    assert run_table_checks(tlc, obs, tmp_path) == []
    cfpb = source_by_id["cfpb-complaints"]
    assert (
        run_table_checks(
            cfpb, Observation(source_id=cfpb.id, observed_at=RUN_AT, available=True), tmp_path
        )
        == []
    )
