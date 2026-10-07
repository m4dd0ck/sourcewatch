"""Inline SVG for the status site: a 90-day strip and a sparkline. No JavaScript."""

from datetime import date
from html import escape

from sourcewatch.types import Status

CELL_W, CELL_H, GAP = 6, 18, 2


def strip_svg(days: list[tuple[date, Status | None]], label: str = "") -> str:
    """One cell per calendar day, oldest on the left; ``None`` is a day with no observation."""
    width = len(days) * (CELL_W + GAP) - GAP
    cells = []
    for index, (day, status) in enumerate(days):
        cls = status.value if status else "none"
        title = f"{day.isoformat()}: {status.value if status else 'no observation'}"
        cells.append(
            f'<rect class="{cls}" x="{index * (CELL_W + GAP)}" y="0" width="{CELL_W}" '
            f'height="{CELL_H}" rx="1"><title>{escape(title)}</title></rect>'
        )
    aria = escape(label or f"{len(days)}-day status strip")
    return (
        f'<svg class="strip" viewBox="0 0 {max(width, 1)} {CELL_H}" role="img" '
        f'aria-label="{aria}" preserveAspectRatio="none">' + "".join(cells) + "</svg>"
    )


def sparkline_svg(
    points: list[tuple[date, float | None]],
    band: tuple[float, float] | None = None,
    label: str = "",
    width: int = 640,
    height: int = 72,
) -> str:
    """A line over time with an optional shaded acceptable band; gaps where values are missing."""
    values = [v for _, v in points if v is not None]
    if not values:
        return (
            f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{escape(label or "no data")}"></svg>'
        )
    top, bottom = 6, 6
    ceiling = max([*values, band[1] if band else 0.0]) * 1.08 or 1.0
    floor = 0.0
    step = (width - 2) / max(len(points) - 1, 1)

    def y(value: float) -> float:
        return top + (1 - (value - floor) / (ceiling - floor)) * (height - top - bottom)

    parts = []
    if band:
        parts.append(
            f'<rect class="band" x="0" y="{y(band[1]):.1f}" width="{width}" '
            f'height="{max(y(band[0]) - y(band[1]), 0.5):.1f}"/>'
        )
    segments: list[list[str]] = [[]]
    for index, (_, value) in enumerate(points):
        if value is None:
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(f"{1 + index * step:.1f},{y(value):.1f}")
    for segment in segments:
        if len(segment) == 1:
            x, yy = segment[0].split(",")
            parts.append(f'<circle class="line" cx="{x}" cy="{yy}" r="1.5"/>')
        elif segment:
            parts.append(f'<polyline class="line" points="{" ".join(segment)}"/>')
    last_day, last_value = next(
        ((d, v) for d, v in reversed(points) if v is not None), (None, None)
    )
    if last_value is not None and last_day is not None:
        parts.append(
            f'<circle class="dot" cx="{1 + (len(points) - 1) * step:.1f}" '
            f'cy="{y(last_value):.1f}" r="2.5"><title>{last_day.isoformat()}: '
            f"{escape(f'{last_value:,.0f}')}</title></circle>"
        )
    aria = escape(label or "sparkline")
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" aria-label="{aria}" '
        f'preserveAspectRatio="none">' + "".join(parts) + "</svg>"
    )
