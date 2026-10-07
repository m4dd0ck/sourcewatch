# sourcewatch

A status page for public datasets. Python, DuckDB, httpx, [observatory](https://github.com/m4dd0ck/observatory), GitHub Actions, GitHub Pages.

Every morning at 11:00 UTC a workflow probes six open data sources that analysts depend on, grades each for availability, freshness against its own cadence, volume against its own history, schema drift and value sanity, appends the observations to a history kept in git, and republishes the site.

**Site:** https://m4dd0ck.github.io/sourcewatch/ · **Machine-readable:** [`status.json`](https://m4dd0ck.github.io/sourcewatch/status.json), [`history.json`](https://m4dd0ck.github.io/sourcewatch/history.json) · **History:** the [`data` branch](https://github.com/m4dd0ck/sourcewatch/tree/data)

## What a run looks like

```
$ uv run sourcewatch run --data-dir data
sourcewatch run 2026-10-07T18:56:33+00:00

source                 avail  fresh  volume  schema  values  status    detail
cfpb-complaints        ok     ok     ok      ok      ok      ok        latest 2026-10-07, 8,752 received on 2026-10-05
nyc-311                ok     ok     ok      ok      ok      ok        latest 2026-10-06 02:05 EDT, 11,481 on 2026-10-05
nyc-tlc                ok     ok     ok      ok      -       ok        yellow 2026-08 published 2026-10-01
open-meteo-archive     ok     ok     ok      ok      ok      ok        archive through 2026-10-07, 24 hourly rows on 2026-10-06
treasury-dts           ok     ok     ok      ok      ok      ok        DTS record_date 2026-10-05 (Mon), 4 rows
usgs-earthquakes       ok     ok     ok      ok      ok      ok        newest event 5 min ago, 24h count 265

6 sources, 32 checks, 0 failed, 0 incidents open. 22.7 s. All 6 sources healthy.
wrote data/history/2026/10/07.parquet
```

When something is wrong the cells say so (`FAIL`, `warn`, `DRIFT`, `-` for not observed), the day turns amber or red on the site's 90-day strip, and after two consecutive failing runs an incident opens with the message above. Incidents close themselves when the source passes again.

## Sources

| Source | Cadence | Requests per run | Probe |
|---|---|---|---|
| [NYC TLC yellow taxi trips](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) | monthly, ok within 45 days of month end | ≤ 5 | HEAD the newest months, then read only the Parquet footer (row count, schema) |
| [CFPB consumer complaints](https://www.consumerfinance.gov/data-research/consumer-complaints/) | daily (UTC) | 2 | newest complaint and field names; count for the newest complete day |
| [Treasury Daily Treasury Statement](https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/) | business days (ET), US federal holidays allowed for | 1 | newest record date, rows on it, types from the API's own metadata |
| [Open-Meteo weather archive](https://open-meteo.com/en/docs/historical-weather-api) | daily (ET) | 1 | last ten days for New York; hourly completeness of the last complete day |
| [NYC 311 service requests](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9) | daily (ET) | 3 | dataset "rows updated" time, count for the newest complete day, 200 newest rows |
| [USGS earthquakes, past day](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php) | continuous, ok within 6 hours | 1 | the all-day GeoJSON feed |

Each source is one YAML file under [`sources/`](sources/) with a sidecar `<id>.schema.json` holding the accepted schema. Thresholds are the author's judgement from watching each source and are easy to argue with in a pull request.

## How the checks work

- **availability**: three attempts with backoff; a 403 or 5xx on the deciding request marks the source `unknown` for the run, which is not a failure until it repeats.
- **freshness**: measured against the source's cadence, in its timezone. Daily sources are compared with yesterday, business-day sources skip weekends and holidays, monthly files are measured in days after month end, feeds in hours. Thresholds give `ok`, `degraded`, `failed`.
- **volume**: today's figure against a band around the rolling median of the last 28 observations, same weekday where that matters. New sources read "learning" until there are enough days; `sourcewatch backfill` seeds the baselines from the source's own history where it exposes one.
- **schema**: field names and types against the accepted sidecar. A change is `DRIFT` with the diff (`+added, -removed, ~retyped`) until someone runs `sourcewatch accept-schema <id>` and commits the result.
- **values**: [observatory](https://github.com/m4dd0ck/observatory) checks on a sample of at most 200 rows: allowed values, nulls, plausible ranges.

Only observations are stored: outcomes, counts, timestamps, schema fingerprints, HTTP details. Never rows from a source. The TLC probe never downloads a trip file. History grows by a few kilobytes a day.

## Run it yourself

```bash
git clone https://github.com/m4dd0ck/sourcewatch && cd sourcewatch
uv sync
uv run sourcewatch run --data-dir data           # ~25 s, 13 HTTP requests in total
uv run sourcewatch site --data-dir data --out site
uv run sourcewatch backfill --data-dir data      # optional: seed volume baselines
uv run sourcewatch accept-schema nyc-tlc          # after a source legitimately changes shape
```

`SOCRATA_APP_TOKEN` is optional; without it the 311 probe runs anonymously and is throttled per IP, which three requests a day never reaches.

## How it is deployed

- `.github/workflows/daily.yml` runs on a cron and on demand. It checks out `main` and the orphan `data` branch (into `data/`), runs the checks, commits the new observations back to `data` as `github-actions[bot]`, renders the site and deploys it to Pages.
- `main` holds only code and configuration; humans commit there. The daily commits never touch it.
- `.github/workflows/ci.yml` lints, type-checks (`mypy --strict`), tests, validates the source definitions and renders the site from the committed 90-day fixture history, so a broken renderer fails CI without touching a source.
- One-time setup: push `main`, run `scripts/init_data_branch.sh`, enable Pages with source "GitHub Actions".

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest                 # no network; every source is a recorded fixture
uv run pytest -m network      # live smoke test of each probe
uv run python -m tests.make_fixture_data   # regenerate the synthetic 90-day history
```

Tests mock HTTP with respx and fail on any request that is not mocked. Dates are injected, never read from the wall clock inside the checks.

## Adding a source

1. Write `sources/<id>.yaml` (see any existing one): a probe kind, a cadence, a volume band, optional observatory `table_checks`, and the request budget.
2. If the probe kind is new, add a module under `src/sourcewatch/probes/` that returns an `Observation`, register it, and record a trimmed real response under `tests/fixtures/responses/`.
3. Run once, then `sourcewatch accept-schema <id>` and commit the sidecar.

## Limitations

- One run a day on shared runners; a run can start late or be skipped. A day with no run is grey, not red.
- Volume bands are statistical, not semantic. A holiday can look like an outage for a day.
- Samples are small, so value checks catch new labels and gross nulls, not rare ones.
- Alerting is the site and `status.json`; there is no push notification yet.

## License

MIT
