# Runbook

Procedures for sourcewatch. `README.md` describes what each command does; this file says when
to run it and in what order.

## On open

```bash
git status --short
git log -3 --oneline
```

## On close

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
uv run sourcewatch validate-config
```

## Daily

Nothing by hand. `daily.yml` runs at 11:00 UTC, commits observations to `data` and deploys the
site. To check it ran: the site's run timestamp, or `gh run list --workflow daily.yml`. To run
it now: `gh workflow run daily.yml`. A day with no run is grey on the strip, not red.

## When a source shows DRIFT

1. Read the diff on the site or in the run log: `+added`, `-removed`, `~retyped`.
2. If the change is legitimate: `uv run sourcewatch run --data-dir data --source <id>` locally
   (or wait for the next daily run), then `uv run sourcewatch accept-schema <id>` and commit
   the sidecar under `sources/`. The next run is green.
3. If the change breaks a probe, fix the probe and record a trimmed real response under
   `tests/fixtures/responses/`.

## Adding a source

1. Write `sources/<id>.yaml` (see any existing one): a probe kind, a cadence, a volume band,
   optional observatory `table_checks`, and the request budget.
2. If the probe kind is new, add a module under `src/sourcewatch/probes/` that returns an
   `Observation`, register it, and record a trimmed real response under
   `tests/fixtures/responses/`.
3. Run once, then `uv run sourcewatch accept-schema <id>` and commit the sidecar.
4. `uv run sourcewatch backfill --data-dir data --source <id>` if the source exposes history,
   so volume grading starts before the window fills.

## Release

1. Bump `version` in `pyproject.toml` and `__version__` in `src/sourcewatch/__init__.py` to the
   same value; `uv lock`.
2. Commit `chore: Release vX.Y.Z`, then `git tag -a vX.Y.Z -m "vX.Y.Z"` and
   `git push origin main --tags`.
3. `gh release create vX.Y.Z --title "vX.Y.Z" --notes "<what changed>"` with the compare link
   to the previous tag, marked latest.
4. CI must be green on the tagged commit, and the next daily run green after it.

## Deploy

The daily workflow deploys. After a change to the renderer, dispatch it (`gh workflow run
daily.yml`) rather than waiting; CI already proved the renderer on the fixture history. A
change to the observatory pin is a `chore(deps)` commit with `uv lock`, then the same.

## Restore on a new machine

1. Clone `https://github.com/m4dd0ck/sourcewatch.git`; `uv sync`; `uv run pytest`.
2. The live history is the `data` branch: `git worktree add data data` (or a second clone) to
   render the real site locally with `uv run sourcewatch site --data-dir data --out site`.
3. Nothing else is needed locally. For a fresh GitHub repo: push `main`, run
   `scripts/init_data_branch.sh`, enable Pages with source "GitHub Actions", add the optional
   `SOCRATA_APP_TOKEN` secret, dispatch `daily.yml` once.

## Things that bite

- `data/` on `main` is a checkout of another branch; never `git add` it there.
- Tests fail on any unmocked HTTP request by design; a new probe needs a recorded fixture, not
  a network call.
- A single failed fetch is not an incident; two consecutive are. Do not "fix" a one-off.

## Validate

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest
uv run sourcewatch validate-config
uv run sourcewatch site --data-dir tests/fixtures/data --out site --at 2026-10-07T11:03:41Z
```
