"""sourcewatch's own checks: availability, cadence-aware freshness, volume band, schema drift."""

import hashlib
import json
from datetime import datetime

from sourcewatch.baselines import Sample, band_for
from sourcewatch.cadence import freshness as grade_freshness
from sourcewatch.config import SchemaFile, SourceConfig
from sourcewatch.observation import Observation, SchemaField
from sourcewatch.outcome import Outcome
from sourcewatch.probes.base import NULL_TYPE
from sourcewatch.types import Severity, Status


def availability(source: SourceConfig, obs: Observation) -> Outcome:
    http = obs.http
    details = http.model_dump() if http else {}
    if obs.available:
        latency = f"{http.latency_ms} ms" if http else "n/a"
        attempts = f", {http.attempts} attempts" if http and http.attempts > 1 else ""
        return Outcome(
            run_at=obs.observed_at,
            source_id=source.id,
            check="availability",
            status=Status.OK,
            severity=Severity.CRITICAL,
            metric=float(http.latency_ms) if http else None,
            unit="ms",
            message=f"HTTP {http.status if http else '-'} in {latency}{attempts}",
            details=details,
        )
    status_text = f"HTTP {http.status}" if http and http.status else "no response"
    attempts = f" after {http.attempts} attempts" if http and http.attempts > 1 else ""
    return Outcome(
        run_at=obs.observed_at,
        source_id=source.id,
        check="availability",
        status=Status.FAILED,
        severity=Severity.CRITICAL,
        metric=float(http.latency_ms) if http else None,
        unit="ms",
        message=f"{status_text}{attempts}: {obs.unavailable_reason}".strip(": "),
        details=details,
    )


def unobserved(source: SourceConfig, obs: Observation, check: str) -> Outcome:
    """The outcome of a check that could not run because the source was unavailable."""
    return Outcome(
        run_at=obs.observed_at,
        source_id=source.id,
        check=check,
        status=Status.UNKNOWN,
        message=f"not observed: {obs.unavailable_reason or 'source unavailable'}",
    )


def freshness(source: SourceConfig, obs: Observation, now: datetime) -> Outcome:
    verdict = grade_freshness(source.cadence, obs, now)
    return Outcome(
        run_at=obs.observed_at,
        source_id=source.id,
        check="freshness",
        status=verdict.status,
        severity=Severity.CRITICAL,
        metric=verdict.age,
        threshold=verdict.ok_within,
        unit=verdict.unit,
        message=verdict.message,
        details={
            "cadence": source.cadence.kind,
            "reference": verdict.reference,
            "fail_after": verdict.fail_after,
            "latest_event_at": obs.latest_event_at.isoformat() if obs.latest_event_at else None,
            "published_at": obs.published_at.isoformat() if obs.published_at else None,
        },
    )


def volume(
    source: SourceConfig, obs: Observation, history: list[Sample], today: datetime
) -> Outcome:
    cfg = source.volume
    label = obs.count_label or cfg.metric
    if obs.count is None:
        return Outcome(
            run_at=obs.observed_at,
            source_id=source.id,
            check="volume",
            status=Status.UNKNOWN,
            unit=label,
            message=f"{label}: no figure reported",
        )
    band = band_for(history, cfg, today.date())
    if band is None:
        have = len(history)
        return Outcome(
            run_at=obs.observed_at,
            source_id=source.id,
            check="volume",
            status=Status.OK,
            metric=obs.count,
            unit=label,
            message=f"{label}: {obs.count:,.0f} (learning, {have}/{cfg.min_samples} samples)",
            details={"learning": True, "samples": have},
        )
    details = {
        "median": band.median,
        "low": band.low,
        "high": band.high,
        "samples": band.samples,
        "weekday_matched": band.weekday_matched,
    }
    if obs.count == 0 and band.median > 0:
        status = Status.FAILED
    elif band.low <= obs.count <= band.high:
        status = Status.OK
    else:
        status = Status.DEGRADED
    direction = "below" if obs.count < band.low else "above" if obs.count > band.high else "within"
    return Outcome(
        run_at=obs.observed_at,
        source_id=source.id,
        check="volume",
        status=status,
        metric=obs.count,
        threshold=band.median,
        unit=label,
        message=(
            f"{label}: {obs.count:,.0f} {direction} band {band.low:,.0f}–{band.high:,.0f} "
            f"(median {band.median:,.0f} of {band.samples})"
        ),
        details=details,
    )


def fingerprint(fields: list[SchemaField]) -> str:
    canonical = json.dumps(sorted((f.name, f.dtype) for f in fields))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def schema_diff(expected: list[SchemaField], actual: list[SchemaField]) -> list[str]:
    """Human-readable differences: ``+added``, ``-removed``, ``~name:OLD→NEW``."""
    before = {f.name: f.dtype for f in expected}
    after = {f.name: f.dtype for f in actual}
    diff = [f"+{name}" for name in after if name not in before]
    diff += [f"-{name}" for name in before if name not in after]
    diff += [
        f"~{name}:{before[name]}→{after[name]}"
        for name in after
        if name in before
        and before[name] != after[name]
        and NULL_TYPE not in (before[name], after[name])
    ]
    return diff


def schema(source: SourceConfig, obs: Observation, expected: SchemaFile | None) -> Outcome:
    fields = obs.fields or []
    if not fields:
        return Outcome(
            run_at=obs.observed_at,
            source_id=source.id,
            check="schema",
            status=Status.UNKNOWN,
            message="source reported no schema",
        )
    actual_fp = fingerprint(fields)
    if expected is None:
        return Outcome(
            run_at=obs.observed_at,
            source_id=source.id,
            check="schema",
            status=Status.OK,
            severity=Severity.INFO,
            metric=float(len(fields)),
            unit="fields",
            message=f"{len(fields)} fields, no accepted schema yet (run accept-schema)",
            details={"fingerprint": actual_fp, "accepted": False, "fields": _dump(fields)},
        )
    diff = schema_diff(expected.fields, fields)
    if not diff:
        return Outcome(
            run_at=obs.observed_at,
            source_id=source.id,
            check="schema",
            status=Status.OK,
            severity=source.schema_.severity,
            metric=float(len(fields)),
            unit="fields",
            message=f"schema unchanged ({len(fields)} fields)",
            details={"fingerprint": actual_fp, "accepted": True, "fields": _dump(fields)},
        )
    severity = source.schema_.severity
    return Outcome(
        run_at=obs.observed_at,
        source_id=source.id,
        check="schema",
        status=Status.FAILED if severity == Severity.CRITICAL else Status.DEGRADED,
        severity=severity,
        metric=float(len(fields)),
        threshold=float(len(expected.fields)),
        unit="fields",
        message="schema changed: " + ", ".join(diff),
        details={
            "fingerprint": actual_fp,
            "expected": expected.fingerprint,
            "diff": diff,
            "fields": _dump(fields),
        },
    )


def _dump(fields: list[SchemaField]) -> list[dict[str, str]]:
    return [f.model_dump() for f in fields]
