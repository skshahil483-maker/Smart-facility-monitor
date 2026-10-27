"""
tests/test_rules.py – Unit tests for app/rules.py.

Tests the pure helper functions (no Flask app context required for most)
and the rules engine integration via process_reading().
"""

import pytest

from app.rules import (
    DRIFT_THRESHOLD_PCT,
    build_drift_message,
    build_threshold_message,
    check_threshold,
    detect_drift,
)


# ── Fake sensor for pure function tests ───────────────────────────────────────

class FakeSensor:
    """Minimal duck-typed sensor for testing pure functions."""

    def __init__(
        self,
        name="Test Sensor",
        location="Test Room",
        unit="°C",
        warning_min=None,
        warning_max=None,
        critical_min=None,
        critical_max=None,
    ):
        self.id = 1
        self.name = name
        self.location = location
        self.unit = unit
        self.warning_min = warning_min
        self.warning_max = warning_max
        self.critical_min = critical_min
        self.critical_max = critical_max


# ── check_threshold ───────────────────────────────────────────────────────────

class TestCheckThreshold:
    """Tests for the check_threshold() pure function."""

    def setup_method(self):
        self.sensor = FakeSensor(
            warning_min=60.0, warning_max=90.0,
            critical_min=50.0, critical_max=110.0,
        )

    def test_normal_value_returns_normal(self):
        assert check_threshold(self.sensor, 75.0) == "normal"

    def test_value_at_warning_max_boundary_is_normal(self):
        """Boundary: exactly at warning_max should be normal (not exceeded)."""
        assert check_threshold(self.sensor, 90.0) == "normal"

    def test_value_just_above_warning_max_is_warning(self):
        assert check_threshold(self.sensor, 90.1) == "warning"

    def test_value_just_below_warning_min_is_warning(self):
        assert check_threshold(self.sensor, 59.9) == "warning"

    def test_value_above_critical_max_is_critical(self):
        assert check_threshold(self.sensor, 115.0) == "critical"

    def test_value_below_critical_min_is_critical(self):
        assert check_threshold(self.sensor, 48.0) == "critical"

    def test_critical_takes_priority_over_warning(self):
        """A value beyond critical should always return 'critical', not 'warning'."""
        assert check_threshold(self.sensor, 200.0) == "critical"

    def test_no_min_threshold_ignores_low_values(self):
        sensor = FakeSensor(warning_min=None, warning_max=90.0,
                            critical_min=None, critical_max=110.0)
        assert check_threshold(sensor, 0.0) == "normal"

    def test_no_max_threshold_ignores_high_values(self):
        sensor = FakeSensor(warning_min=60.0, warning_max=None,
                            critical_min=50.0, critical_max=None)
        assert check_threshold(sensor, 999.0) == "normal"

    def test_all_thresholds_none_is_always_normal(self):
        sensor = FakeSensor()
        assert check_threshold(sensor, 999999.0) == "normal"


# ── detect_drift ──────────────────────────────────────────────────────────────

class TestDetectDrift:
    """Tests for the detect_drift() pure function."""

    def test_no_drift_similar_means(self):
        recent = [75.0, 74.5, 75.5, 75.0, 75.2]
        baseline = [75.0] * 20
        assert detect_drift(recent, baseline) is False

    def test_drift_detected_when_deviation_exceeds_threshold(self):
        baseline = [100.0] * 20
        # Recent mean = 120 → 20% deviation → drift
        recent = [120.0] * 5
        assert detect_drift(recent, baseline) is True

    def test_drift_at_exactly_threshold_is_false(self):
        """Deviation must be strictly greater than DRIFT_THRESHOLD_PCT."""
        baseline = [100.0] * 20
        exactly_at_threshold = 100.0 * (1 + DRIFT_THRESHOLD_PCT)
        recent = [exactly_at_threshold] * 5
        # Should NOT trigger (must exceed, not equal)
        assert detect_drift(recent, baseline) is False

    def test_drift_just_above_threshold_is_true(self):
        baseline = [100.0] * 20
        just_above = 100.0 * (1 + DRIFT_THRESHOLD_PCT + 0.001)
        recent = [just_above] * 5
        assert detect_drift(recent, baseline) is True

    def test_downward_drift_detected(self):
        baseline = [100.0] * 20
        recent = [80.0] * 5  # 20% below
        assert detect_drift(recent, baseline) is True

    def test_empty_recent_is_false(self):
        assert detect_drift([], [100.0] * 20) is False

    def test_empty_baseline_is_false(self):
        assert detect_drift([100.0] * 5, []) is False

    def test_zero_baseline_mean_is_false(self):
        """Prevent division by zero when baseline mean is 0."""
        assert detect_drift([1.0, 2.0, 3.0, 4.0, 5.0], [0.0] * 20) is False


# ── Message builders ──────────────────────────────────────────────────────────

class TestMessageBuilders:
    """Tests for the message-building helper functions."""

    def setup_method(self):
        self.sensor = FakeSensor(name="Boiler", location="Boiler Room", unit="°C")

    def test_threshold_message_contains_severity(self):
        msg = build_threshold_message(self.sensor, 95.0, "warning")
        assert "WARNING" in msg
        assert "95.00" in msg
        assert "°C" in msg

    def test_threshold_message_contains_critical(self):
        msg = build_threshold_message(self.sensor, 115.0, "critical")
        assert "CRITICAL" in msg

    def test_drift_message_contains_drift(self):
        msg = build_drift_message(self.sensor, 115.0, 100.0)
        assert "DRIFT" in msg
        assert "15.0%" in msg


# ── Integration: process_reading (requires app context) ───────────────────────

class TestProcessReading:
    """Integration tests for process_reading() using the real DB."""

    def test_normal_reading_creates_no_alerts(self, app):
        """A reading within normal range should not create any alerts."""
        from app import db
        from app.models import Alert, Reading, Sensor
        from app.rules import process_reading

        with app.app_context():
            sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
            assert sensor is not None, "Seed sensor not found"

            reading = Reading(sensor_id=sensor.id, value=75.0)
            db.session.add(reading)
            db.session.commit()

            alerts = process_reading(sensor, reading)
            assert alerts == []

    def test_warning_reading_creates_warning_alert(self, app):
        """A value above warning_max should create a warning alert."""
        from app import db
        from app.models import Alert, Reading, Sensor
        from app.rules import process_reading

        with app.app_context():
            sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()

            reading = Reading(sensor_id=sensor.id, value=95.0)  # above warning_max=90
            db.session.add(reading)
            db.session.commit()

            alerts = process_reading(sensor, reading)
            warning_alerts = [a for a in alerts if a.severity == "warning"]
            assert len(warning_alerts) >= 1

    def test_critical_reading_creates_critical_alert(self, app):
        """A value above critical_max should create a critical alert."""
        from app import db
        from app.models import Alert, Reading, Sensor
        from app.rules import process_reading

        with app.app_context():
            sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()

            reading = Reading(sensor_id=sensor.id, value=115.0)  # above critical_max=110
            db.session.add(reading)
            db.session.commit()

            alerts = process_reading(sensor, reading)
            critical_alerts = [a for a in alerts if a.severity == "critical"]
            assert len(critical_alerts) >= 1

    def test_duplicate_open_alert_is_suppressed(self, app):
        """A second reading in warning range should not create a second open alert."""
        from app import db
        from app.models import Alert, Reading, Sensor
        from app.rules import process_reading

        with app.app_context():
            sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()

            # Resolve all existing open alerts first so we start clean
            Alert.query.filter_by(
                sensor_id=sensor.id, severity="warning", status="open"
            ).update({"status": "resolved"})
            db.session.commit()

            # First reading in warning range → should create 1 alert
            r1 = Reading(sensor_id=sensor.id, value=92.0)
            db.session.add(r1)
            db.session.commit()
            alerts1 = process_reading(sensor, r1)
            warning_count_1 = len([a for a in alerts1 if a.severity == "warning"])

            # Second reading in warning range → should NOT create another
            r2 = Reading(sensor_id=sensor.id, value=93.0)
            db.session.add(r2)
            db.session.commit()
            alerts2 = process_reading(sensor, r2)
            warning_count_2 = len([a for a in alerts2 if a.severity == "warning"])

            assert warning_count_1 == 1
            assert warning_count_2 == 0

    def test_drift_requires_five_recent_and_twenty_baseline_readings(self, app):
        """Only compare complete 5-reading and 20-reading drift windows."""
        from datetime import datetime, timedelta

        from app import db
        from app.models import Reading, Sensor
        from app.rules import process_reading

        with app.app_context():
            sensor = Sensor(
                name="Drift Window Test Sensor",
                type="temperature",
                location="Test Lab",
                unit="C",
            )
            db.session.add(sensor)
            db.session.flush()
            start = datetime(2024, 1, 1)

            for index in range(24):
                db.session.add(
                    Reading(
                        sensor_id=sensor.id,
                        value=100.0 if index < 20 else 120.0,
                        timestamp=start + timedelta(seconds=index),
                    )
                )
            db.session.commit()

            # At 24 total measurements, one complete 5-reading window is absent.
            newest = Reading(
                sensor_id=sensor.id,
                value=120.0,
                timestamp=start + timedelta(seconds=24),
            )
            db.session.add(newest)
            db.session.commit()

            alerts = process_reading(sensor, newest)
            assert [alert.severity for alert in alerts] == ["drift"]

            repeated = Reading(
                sensor_id=sensor.id,
                value=120.0,
                timestamp=start + timedelta(seconds=25),
            )
            db.session.add(repeated)
            db.session.commit()
            assert process_reading(sensor, repeated) == []
