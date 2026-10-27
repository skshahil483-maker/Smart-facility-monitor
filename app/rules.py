"""
app/rules.py – Threshold and drift detection rules engine.

Threshold and drift calculations are pure functions. The integration helper
``process_reading`` loads the needed history and persists generated alerts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    # Only imported for type checking – avoids circular imports at runtime.
    from app.models import Alert, Reading, Sensor

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Drift detection window sizes
DRIFT_RECENT_N: int = 5    # Compare the last N readings …
DRIFT_BASELINE_N: int = 20  # … against the previous M readings
DRIFT_THRESHOLD_PCT: float = 0.15  # 15 % deviation triggers a drift alert


# ── Pure helper functions (no DB access) ─────────────────────────────────────

def check_threshold(sensor: "Sensor", value: float) -> str:
    """
    Evaluate *value* against the sensor's configured thresholds.

    Priority: critical > warning > normal.

    Args:
        sensor: A Sensor ORM object (or any duck-typed object with the
                same threshold attributes).
        value:  The measured value to evaluate.

    Returns:
        'critical' | 'warning' | 'normal'
    """
    # Critical check first (higher priority)
    if sensor.critical_max is not None and value > sensor.critical_max:
        return "critical"
    if sensor.critical_min is not None and value < sensor.critical_min:
        return "critical"

    # Warning check
    if sensor.warning_max is not None and value > sensor.warning_max:
        return "warning"
    if sensor.warning_min is not None and value < sensor.warning_min:
        return "warning"

    return "normal"


def build_threshold_message(sensor: "Sensor", value: float, severity: str) -> str:
    """
    Build a human-readable alert message for a threshold violation.

    Args:
        sensor:   The sensor that triggered the alert.
        value:    The violating value.
        severity: 'warning' or 'critical'.

    Returns:
        A descriptive message string.
    """
    return (
        f"[{severity.upper()}] {sensor.name} ({sensor.location}) "
        f"reading {value:.2f} {sensor.unit} has crossed the "
        f"{severity} threshold."
    )


def detect_drift(recent_values: list[float], baseline_values: list[float]) -> bool:
    """
    Detect gradual drift by comparing two windows of readings.

    A drift is flagged when the mean of *recent_values* deviates from the
    mean of *baseline_values* by more than DRIFT_THRESHOLD_PCT.

    Args:
        recent_values:   The most recent DRIFT_RECENT_N readings (newest last).
        baseline_values: The previous DRIFT_BASELINE_N readings.

    Returns:
        True if drift is detected; False otherwise.
    """
    if not recent_values or not baseline_values:
        return False

    recent_mean = sum(recent_values) / len(recent_values)
    baseline_mean = sum(baseline_values) / len(baseline_values)

    # Avoid division by zero when baseline mean is exactly 0
    if baseline_mean == 0:
        return False

    deviation = abs(recent_mean - baseline_mean) / abs(baseline_mean)
    logger.debug(
        "Drift check: recent_mean=%.3f baseline_mean=%.3f deviation=%.1f%%",
        recent_mean,
        baseline_mean,
        deviation * 100,
    )
    return deviation > DRIFT_THRESHOLD_PCT


def build_drift_message(sensor: "Sensor", recent_mean: float, baseline_mean: float) -> str:
    """Build a human-readable drift alert message."""
    pct = abs(recent_mean - baseline_mean) / abs(baseline_mean) * 100 if baseline_mean else 0
    return (
        f"[DRIFT] {sensor.name} ({sensor.location}) shows a gradual drift. "
        f"Recent average {recent_mean:.2f} {sensor.unit} deviates "
        f"{pct:.1f}% from the baseline {baseline_mean:.2f} {sensor.unit}. "
        f"Predictive maintenance recommended."
    )


def has_open_alert(sensor_id: int, severity: str) -> bool:
    """
    Check if an open alert already exists for this sensor + severity.

    Prevents duplicate alert spam.

    Args:
        sensor_id: Primary key of the sensor.
        severity:  'warning' | 'critical' | 'drift'.

    Returns:
        True if a matching open alert already exists.
    """
    from app.models import Alert  # local import to avoid circular dep at module load

    return (
        Alert.query.filter_by(
            sensor_id=sensor_id,
            severity=severity,
            status="open",
        ).first()
        is not None
    )


# ── Integration point ─────────────────────────────────────────────────────────

def process_reading(sensor: "Sensor", reading: "Reading") -> list["Alert"]:
    """
    Run all rules against a new reading and persist any new alerts.

    This is the **only** function in this module that touches the database.
    Call it *after* committing the Reading to the database.

    Rules executed (in order):
        1. Threshold check  → warning or critical alert
        2. Drift detection  → drift alert (predictive maintenance)

    Args:
        sensor:  The Sensor that produced the reading.
        reading: The freshly committed Reading object.

    Returns:
        A list of newly created Alert objects (may be empty).
    """
    from app import db
    from app.models import Alert

    new_alerts: list[Alert] = []

    # ── Rule 1: Threshold check ───────────────────────────────────────────────
    severity = check_threshold(sensor, reading.value)

    if severity in ("warning", "critical"):
        if not has_open_alert(sensor.id, severity):
            msg = build_threshold_message(sensor, reading.value, severity)
            alert = Alert(
                sensor_id=sensor.id,
                reading_id=reading.id,
                severity=severity,
                message=msg,
                status="open",
            )
            db.session.add(alert)
            new_alerts.append(alert)
            logger.info("Alert created: sensor=%d severity=%s", sensor.id, severity)
        else:
            logger.debug(
                "Duplicate alert suppressed: sensor=%d severity=%s", sensor.id, severity
            )

    # ── Rule 2: Drift detection ───────────────────────────────────────────────
    # Fetch enough readings for the drift window (recent + baseline)
    total_needed = DRIFT_RECENT_N + DRIFT_BASELINE_N
    from app.models import Reading as ReadingModel

    recent_rows = (
        ReadingModel.query.filter_by(sensor_id=sensor.id)
        .order_by(ReadingModel.timestamp.desc(), ReadingModel.id.desc())
        .limit(total_needed)
        .all()
    )

    if len(recent_rows) == total_needed:
        # Rows are newest-first; recent = first five, baseline = prior twenty.
        recent_vals = [r.value for r in recent_rows[:DRIFT_RECENT_N]]
        baseline_vals = [
            r.value for r in recent_rows[DRIFT_RECENT_N:total_needed]
        ]

        if detect_drift(recent_vals, baseline_vals):
            if not has_open_alert(sensor.id, "drift"):
                recent_mean = sum(recent_vals) / len(recent_vals)
                baseline_mean = sum(baseline_vals) / len(baseline_vals)
                msg = build_drift_message(sensor, recent_mean, baseline_mean)
                drift_alert = Alert(
                    sensor_id=sensor.id,
                    reading_id=reading.id,
                    severity="drift",
                    message=msg,
                    status="open",
                )
                db.session.add(drift_alert)
                new_alerts.append(drift_alert)
                logger.info("Drift alert created: sensor=%d", sensor.id)

    db.session.commit()
    return new_alerts
