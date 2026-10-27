"""
app/charts.py – Matplotlib chart generation helpers.

Charts are rendered to in-memory PNG bytes and served directly from
Flask routes – no files written to disk, no JavaScript required.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend (no GUI window)
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.figure import Figure

if TYPE_CHECKING:
    from app.models import Sensor


def generate_sensor_chart(sensor: "Sensor", readings: list) -> bytes:
    """
    Generate a trend chart PNG for a sensor's readings.

    Draws:
        - The value time-series as a blue line
        - Warning threshold band (yellow, semi-transparent)
        - Critical threshold band (red, semi-transparent)
        - Horizontal dashed lines for each threshold

    Args:
        sensor:   The Sensor ORM object (used for thresholds and labels).
        readings: List of Reading objects ordered by timestamp ascending.

    Returns:
        PNG image as raw bytes.
    """
    if not readings:
        return _empty_chart(sensor.name)

    timestamps = [r.timestamp for r in readings]
    values = [r.value for r in readings]

    # ── Figure setup ─────────────────────────────────────────────────────────
    fig: Figure = plt.figure(figsize=(10, 4), dpi=100)
    ax = fig.add_subplot(111)

    # ── Main value line ───────────────────────────────────────────────────────
    ax.plot(
        timestamps,
        values,
        color="#1a73e8",
        linewidth=1.8,
        marker="o",
        markersize=3,
        label=f"{sensor.name} ({sensor.unit})",
        zorder=3,
    )

    y_min = min(values)
    y_max = max(values)
    padding = (y_max - y_min) * 0.15 if y_max != y_min else 1.0

    # ── Threshold lines and shading ───────────────────────────────────────────
    _draw_threshold(ax, sensor.warning_min, "warning_min", "#f9a825", "--", y_min, y_max)
    _draw_threshold(ax, sensor.warning_max, "warning_max", "#f9a825", "--", y_min, y_max)
    _draw_threshold(ax, sensor.critical_min, "critical_min", "#d32f2f", "-.", y_min, y_max)
    _draw_threshold(ax, sensor.critical_max, "critical_max", "#d32f2f", "-.", y_min, y_max)

    # Fill warning zone
    w_lo = sensor.warning_min if sensor.warning_min is not None else y_min - padding
    w_hi = sensor.warning_max if sensor.warning_max is not None else y_max + padding
    ax.axhspan(w_lo, w_hi, alpha=0.08, color="#f9a825", label="Warning zone", zorder=0)

    # ── Axes formatting ───────────────────────────────────────────────────────
    ax.set_title(
        f"{sensor.name} — {sensor.location}",
        fontsize=13,
        fontweight="bold",
        pad=10,
    )
    ax.set_xlabel("Time", fontsize=10)
    ax.set_ylabel(sensor.unit, fontsize=10)
    ax.set_ylim(y_min - padding, y_max + padding)

    # Format x-axis as readable dates/times
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M\n%d %b"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=0, ha="center")

    ax.grid(True, linestyle=":", alpha=0.5, color="grey")
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()

    # ── Render to bytes ───────────────────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _draw_threshold(
    ax,
    value: float | None,
    label: str,
    color: str,
    linestyle: str,
    y_min: float,
    y_max: float,
) -> None:
    """Draw a single horizontal threshold line if the value is not None."""
    if value is None:
        return
    ax.axhline(
        y=value,
        color=color,
        linestyle=linestyle,
        linewidth=1.2,
        alpha=0.85,
        label=label,
        zorder=2,
    )


def _empty_chart(sensor_name: str) -> bytes:
    """Return a placeholder PNG when there are no readings."""
    fig: Figure = plt.figure(figsize=(10, 4), dpi=100)
    ax = fig.add_subplot(111)
    ax.text(
        0.5,
        0.5,
        f"No readings for {sensor_name!r} yet.",
        ha="center",
        va="center",
        fontsize=14,
        color="grey",
        transform=ax.transAxes,
    )
    ax.set_title(sensor_name)
    ax.axis("off")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
