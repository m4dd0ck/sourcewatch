import httpx
import pytest
import respx

from sourcewatch.errors import ProbeError
from sourcewatch.http import USER_AGENT, fetch, get_json, head, make_client
from tests.conftest import no_sleep

URL = "https://example.org/data.json"


def test_client_sends_an_honest_user_agent(mock: respx.MockRouter):
    route = mock.get(URL).respond(200, json={"ok": True})
    with make_client() as client:
        body, fetched = get_json(client, URL, sleep=no_sleep)
    assert body == {"ok": True}
    assert fetched.ok and fetched.detail.status == 200 and fetched.detail.attempts == 1
    assert route.calls.last.request.headers["user-agent"] == USER_AGENT
    assert "sourcewatch/" in USER_AGENT and "github.com/m4dd0ck/sourcewatch" in USER_AGENT


def test_retries_on_503_then_reports_the_final_status(mock: respx.MockRouter, client: httpx.Client):
    route = mock.get(URL).respond(503)
    fetched = fetch(client, "GET", URL, sleep=no_sleep)
    assert not fetched.ok
    assert fetched.detail.status == 503
    assert fetched.detail.attempts == 3
    assert route.call_count == 3


def test_get_json_raises_probe_error_on_non_2xx(mock: respx.MockRouter, client: httpx.Client):
    mock.get(URL).respond(404)
    with pytest.raises(ProbeError) as exc:
        get_json(client, URL, sleep=no_sleep)
    assert exc.value.detail is not None and exc.value.detail.status == 404


def test_transport_errors_are_retried_then_raised(mock: respx.MockRouter, client: httpx.Client):
    route = mock.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(ProbeError, match="after 3 attempts"):
        fetch(client, "GET", URL, sleep=no_sleep)
    assert route.call_count == 3


def test_body_size_cap(mock: respx.MockRouter, client: httpx.Client):
    mock.get(URL).respond(200, content=b"x" * 2048)
    with pytest.raises(ProbeError, match="exceeded 1,024 bytes"):
        fetch(client, "GET", URL, max_bytes=1024, sleep=no_sleep)


def test_not_json_is_a_probe_error(mock: respx.MockRouter, client: httpx.Client):
    mock.get(URL).respond(200, content=b"<html>")
    with pytest.raises(ProbeError, match="not JSON"):
        get_json(client, URL, sleep=no_sleep)


def test_head_treats_404_as_an_answer(mock: respx.MockRouter, client: httpx.Client):
    mock.head(URL).respond(404)
    fetched = head(client, URL, sleep=no_sleep)
    assert fetched.detail.status == 404 and not fetched.ok and fetched.body == b""
