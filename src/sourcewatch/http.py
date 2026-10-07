"""The one HTTP client: honest User-Agent, timeouts, bounded retries, bounded body size."""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from sourcewatch import __version__
from sourcewatch.errors import ProbeError
from sourcewatch.observation import HttpDetail

USER_AGENT = f"sourcewatch/{__version__} (+https://github.com/m4dd0ck/sourcewatch)"
DEFAULT_TIMEOUT = 20.0
MAX_BYTES = 20_000_000
ATTEMPTS = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class Fetched:
    """A completed request: its detail, headers and (for GET) its body."""

    detail: HttpDetail
    headers: Mapping[str, str]
    body: bytes = b""

    @property
    def ok(self) -> bool:
        return self.detail.status is not None and 200 <= self.detail.status < 300


def make_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """A client every probe shares for one run."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*;q=0.5"},
        timeout=timeout,
        follow_redirects=True,
    )


def _backoff(attempt: int) -> float:
    return float(0.5 * 2 ** (attempt - 1))


def fetch(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    max_bytes: int = MAX_BYTES,
    attempts: int = ATTEMPTS,
    sleep: Sleeper = time.sleep,
) -> Fetched:
    """Perform a request with retries on transport errors and retryable statuses.

    Never raises on an HTTP status: the caller reads ``Fetched.ok`` and ``detail.status``.

    Raises:
        ProbeError: when every attempt failed at the transport level, or the body exceeded
            ``max_bytes``.
    """
    started = time.monotonic()
    last_error: str | None = None
    status: int | None = None
    final_url = url
    for attempt in range(1, attempts + 1):
        try:
            with client.stream(method, url, params=params, headers=headers) as response:
                status = response.status_code
                final_url = str(response.url)
                if status in RETRY_STATUSES and attempt < attempts:
                    sleep(_backoff(attempt))
                    continue
                chunks: list[bytes] = []
                size = 0
                if method.upper() != "HEAD":
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            detail = _detail(final_url, status, started, attempt, "body too large")
                            raise ProbeError(
                                f"response exceeded {max_bytes:,} bytes from {url}", detail
                            )
                        chunks.append(chunk)
                return Fetched(
                    detail=_detail(final_url, status, started, attempt, None),
                    headers=dict(response.headers),
                    body=b"".join(chunks),
                )
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            status = None
            if attempt < attempts:
                sleep(_backoff(attempt))
    detail = _detail(final_url, status, started, attempts, last_error or "retries exhausted")
    raise ProbeError(f"{method} {url} failed after {attempts} attempts: {detail.error}", detail)


def _detail(
    url: str, status: int | None, started: float, attempts: int, error: str | None
) -> HttpDetail:
    return HttpDetail(
        url=url,
        status=status,
        latency_ms=int((time.monotonic() - started) * 1000),
        attempts=attempts,
        error=error,
    )


def head(client: httpx.Client, url: str, *, sleep: Sleeper = time.sleep) -> Fetched:
    """HEAD a URL; a 404 or 403 is a normal answer here, not an error."""
    return fetch(client, "HEAD", url, sleep=sleep)


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    max_bytes: int = MAX_BYTES,
    sleep: Sleeper = time.sleep,
) -> tuple[Any, Fetched]:
    """GET a JSON document.

    Raises:
        ProbeError: on transport failure, a non-2xx final status, or a body that is not JSON.
    """
    fetched = fetch(
        client, "GET", url, params=params, headers=headers, max_bytes=max_bytes, sleep=sleep
    )
    if not fetched.ok:
        raise ProbeError(f"GET {url} returned HTTP {fetched.detail.status}", fetched.detail)
    try:
        return json.loads(fetched.body), fetched
    except json.JSONDecodeError as exc:
        raise ProbeError(
            f"GET {url} returned a body that is not JSON: {exc}", fetched.detail
        ) from exc
