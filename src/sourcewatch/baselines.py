"""Volume baselines: a band around the rolling median of a source's own history."""

import statistics
from dataclasses import dataclass
from datetime import date

from sourcewatch.config import VolumeConfig


@dataclass(frozen=True)
class Sample:
    """One historical value of a volume metric, by the day it describes."""

    day: date
    value: float


@dataclass(frozen=True)
class Band:
    median: float
    low: float
    high: float
    samples: int
    weekday_matched: bool


def select_window(
    history: list[Sample], cfg: VolumeConfig, today: date
) -> tuple[list[float], bool]:
    """The most recent ``cfg.window`` values, same-weekday when asked for and when enough exist."""
    ordered = sorted((s for s in history if s.day < today), key=lambda s: s.day)
    if cfg.weekday_aware:
        same = [s.value for s in ordered if s.day.weekday() == today.weekday()]
        if len(same) >= cfg.min_samples:
            return same[-cfg.window :], True
    return [s.value for s in ordered][-cfg.window :], False


def band_for(history: list[Sample], cfg: VolumeConfig, today: date) -> Band | None:
    """The acceptable band for today's value, or None while the source is still being learned."""
    values, matched = select_window(history, cfg, today)
    if len(values) < cfg.min_samples:
        return None
    median = float(statistics.median(values))
    low, high = cfg.band
    return Band(median, median * low, median * high, len(values), matched)
