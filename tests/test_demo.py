"""Tests for hosted demo generation and reading retention."""

from datetime import timedelta

from app import db
from app.demo import DemoReadingGenerator, prune_old_readings, run_demo_cycle
from app.models import Alert, Reading, Sensor, utc_now


def test_demo_mode_is_disabled_by_default(app):
    assert app.config["DEMO_MODE"] is False
    assert app.config["DEMO_INTERVAL_SECONDS"] == 5
    assert app.config["DATA_RETENTION_HOURS"] == 24


def test_demo_cycle_generates_readings_for_active_sensors(app):
    app.config["DEMO_MODE"] = True
    generator = DemoReadingGenerator()

    generated = run_demo_cycle(app, generator)

    with app.app_context():
        active_sensor = Sensor.query.filter_by(
            name="Test Temperature Sensor"
        ).one()
        inactive_sensor = Sensor.query.filter_by(name="Inactive Sensor").one()
        assert generated == 1
        assert Reading.query.filter_by(sensor_id=active_sensor.id).count() == 1
        assert Reading.query.filter_by(sensor_id=inactive_sensor.id).count() == 0


def test_demo_thread_starts_only_once(app, monkeypatch):
    import app.demo as demo

    starts: list[int] = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            assert callable(target)
            assert args == (app,)
            assert name == "smartfacility-demo"
            assert daemon is True

        def start(self):
            starts.append(1)

    monkeypatch.setattr(demo.threading, "Thread", FakeThread)
    monkeypatch.setattr(demo, "_thread_started", False)
    monkeypatch.setattr(app, "debug", False)
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    app.config["DEMO_MODE"] = True

    assert demo.start_demo_thread(app) is True
    assert demo.start_demo_thread(app) is False
    assert len(starts) == 1


def test_prune_removes_expired_readings_and_preserves_alerts(app):
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").one()
        old_reading = Reading(
            sensor_id=sensor.id,
            value=75.0,
            timestamp=utc_now() - timedelta(hours=2),
        )
        current_reading = Reading(sensor_id=sensor.id, value=76.0)
        db.session.add_all([old_reading, current_reading])
        db.session.commit()

        old_alert = Alert(
            sensor_id=sensor.id,
            reading_id=old_reading.id,
            severity="warning",
            message="Historical alert",
        )
        db.session.add(old_alert)
        db.session.commit()
        old_reading_id = old_reading.id
        current_reading_id = current_reading.id
        alert_id = old_alert.id

        deleted = prune_old_readings(retention_hours=1)

        assert deleted == 1
        assert db.session.get(Reading, old_reading_id) is None
        assert db.session.get(Reading, current_reading_id) is not None
        assert db.session.get(Alert, alert_id).reading_id is None


def test_demo_thread_is_not_started_when_disabled(app, monkeypatch):
    import app.demo as demo

    def unexpected_thread(*args, **kwargs):
        raise AssertionError("Thread should not be constructed when demo is off")

    monkeypatch.setattr(demo.threading, "Thread", unexpected_thread)
    monkeypatch.setattr(demo, "_thread_started", False)
    app.config["DEMO_MODE"] = False

    assert demo.start_demo_thread(app) is False


def test_demo_thread_waits_for_werkzeug_reloader_child(app, monkeypatch):
    import app.demo as demo

    def unexpected_thread(*args, **kwargs):
        raise AssertionError("The reloader parent must not start a demo thread")

    monkeypatch.setattr(demo.threading, "Thread", unexpected_thread)
    monkeypatch.setattr(demo, "_thread_started", False)
    monkeypatch.setattr(app, "debug", True)
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "false")
    app.config["DEMO_MODE"] = True

    assert demo.start_demo_thread(app) is False
