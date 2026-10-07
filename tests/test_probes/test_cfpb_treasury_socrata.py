from datetime import UTC, datetime

import pytest
import respx

from sourcewatch.config import SourceConfig
from sourcewatch.errors import ProbeError
from sourcewatch.probes import PROBES
from sourcewatch.probes.base import ProbeContext
from tests.conftest import load_response

CFPB = "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"
TREASURY = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/"
SOCRATA_VIEWS = "https://data.cityofnewyork.us/api/views/erm2-nwe9.json"
SOCRATA_RESOURCE = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"


def test_cfpb_probe_uses_two_requests(
    mock: respx.MockRouter, ctx: ProbeContext, source_by_id: dict[str, SourceConfig]
):
    latest = mock.get(CFPB, params__contains={"sort": "created_date_desc"}).respond(
        200, json=load_response("cfpb/latest.json")
    )
    counted = mock.get(CFPB, params__contains={"size": "0"}).respond(
        200, json=load_response("cfpb/count_day.json")
    )

    obs = PROBES["cfpb"](source_by_id["cfpb-complaints"], ctx)

    assert latest.called and counted.called and ctx.requests_made == 2
    day_params = counted.calls.last.request.url.params
    assert day_params["date_received_min"] == day_params["date_received_max"] == "2026-10-05"
    assert obs.latest_event_at == datetime(2026, 10, 7, 3, 58, 44, tzinfo=UTC)
    assert obs.count == 8752
    assert {"product", "company", "date_received"} <= {f.name for f in obs.fields or []}
    assert obs.notes == "latest 2026-10-07, 8,752 received on 2026-10-05"


def test_treasury_probe_reads_schema_from_meta(
    mock: respx.MockRouter, ctx: ProbeContext, source_by_id: dict[str, SourceConfig]
):
    route = mock.get(url__startswith=TREASURY).respond(
        200, json=load_response("treasury/latest.json")
    )

    obs = PROBES["treasury"](source_by_id["treasury-dts"], ctx)

    assert route.calls.last.request.url.params["page[size]"] == "50"
    assert obs.latest_event_at == datetime(2026, 10, 5, 4, tzinfo=UTC)  # midnight ET
    assert obs.count == 4
    assert {f.name: f.dtype for f in obs.fields or []}["record_date"] == "DATE"
    assert obs.notes == "DTS record_date 2026-10-05 (Mon), 4 rows"


def test_socrata_probe_three_requests_and_optional_token(
    mock: respx.MockRouter, ctx: ProbeContext, source_by_id: dict[str, SourceConfig]
):
    views = mock.get(SOCRATA_VIEWS).respond(200, json=load_response("socrata/views.json"))
    counted = mock.get(SOCRATA_RESOURCE, params__contains={"$select": "count(*)"}).respond(
        200, json=load_response("socrata/count.json")
    )
    sample = mock.get(SOCRATA_RESOURCE, params__contains={"$limit": "200"}).respond(
        200, json=load_response("socrata/sample.json")
    )
    ctx.env = {"SOCRATA_APP_TOKEN": "secret-token"}

    obs = PROBES["socrata"](source_by_id["nyc-311"], ctx)

    assert views.called and counted.called and sample.called and ctx.requests_made == 3
    assert counted.calls.last.request.headers["x-app-token"] == "secret-token"
    where = counted.calls.last.request.url.params["$where"]
    assert where.startswith("created_date between '2026-10-05T00:00:00'")
    assert obs.published_at == datetime.fromtimestamp(1791337130, tz=UTC)
    assert obs.latest_event_at == datetime(2026, 10, 6, 6, 5, 43, tzinfo=UTC)
    assert obs.count == 355
    assert len(obs.fields or []) == 48
    assert obs.notes == "latest 2026-10-06 02:05 EDT, 355 on 2026-10-05"


def test_socrata_without_token_sends_no_header(
    mock: respx.MockRouter, ctx: ProbeContext, source_by_id: dict[str, SourceConfig]
):
    views = mock.get(SOCRATA_VIEWS).respond(200, json=load_response("socrata/views.json"))
    mock.get(SOCRATA_RESOURCE).respond(200, json=load_response("socrata/sample.json"))
    PROBES["socrata"](source_by_id["nyc-311"], ctx)
    assert "x-app-token" not in views.calls.last.request.headers


def test_budget_is_enforced(
    mock: respx.MockRouter, ctx: ProbeContext, source_by_id: dict[str, SourceConfig]
):
    mock.get(SOCRATA_VIEWS).respond(200, json=load_response("socrata/views.json"))
    mock.get(SOCRATA_RESOURCE).respond(200, json=load_response("socrata/sample.json"))
    ctx.budget = 1
    with pytest.raises(ProbeError, match="budget of 1 exhausted"):
        PROBES["socrata"](source_by_id["nyc-311"], ctx)
