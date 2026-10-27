"""In-process sensor-data generator for hosted demo deployments."""

from __future__ import annotations

import logging
import math
import os
import random
import threading
from typing import TYPE_CHECKING

from app import db

if TYPE_CHECKING:
    from flask import Flask
    from app.models import Sensor

logger = logging.getLogger(__name__)

_BASELINES: dict[str, tuple[float, float]] = {
    "temperature": (75.0, 2.0),
    "pressure": (6.0, 0.3),
    "humidity": (50.0, 3.0),
    "gas": (400.0, 50.0),
    "vibration": (2.0, 0.4),
}
_SPIKE_RATE = 0.05
_DRIFT_INCREMENT = 0.005
_PRUNE_EVERY_CYCLES = 100
_thread_lock = threading.Lock()
_thread_started = False


class DemoReadingGenerator:
    """Keep per-sensor drift offsets and a shared variation cycle."""

    def __init__(self) -> None:
        self.cycle = 0
        self.drift_offsets: dict[int, float] = {}

    def next_cycle(self) -> None:
        """Advance the slow sinusoidal variation phase."""
        self.cycle += 1

    def value_for(self, sensor: Sensor) -> float:
        """Generate a noisy reading with occasional spikes and gradual drift."""
        base, noise = _BASELINES.get(sensor.type, (50.0, 5.0))
        value = (
            base
            + math.sin(self.cycle * 2 * math.pi / 60) * noise * 0.5
            + random.gauss(0, noise * 0.3)
        )

        drift = self.drift_offsets.get(sensor.id, 0.0)
        reference = sensor.warning_max or base * 1.2
        drift += reference * _DRIFT_INCREMENT
        self.drift_offsets[sensor.id] = drift
        value += drift

        if random.random() < _SPIKE_RATE:
            multiplier = random.uniform(1.2, 1.6)
            value = (
                sensor.warning_max * multiplier
                if sensor.warning_max
                else value * multiplier
            )
            logger.info("Demo spike generated for sensor %s", sensor.name)

        return round(value, 3)


def run_demo_cycle(
    app: Flask,
    generator: DemoReadingGenerator | None = None,
) -> int:
    """Generate and process one reading for each active sensor."""
    from app.models import Reading, Sensor
    from app.rules import process_reading

    state = generator or DemoReadingGenerator()
    state.next_cycle()
    created = 0

    with app.app_context():
        sensors = Sensor.query.filter_by(is_active=True).order_by(Sensor.id).all()
        for sensor in sensors:
            reading = Reading(
                sensor_id=sensor.id,
                value=state.value_for(sensor),
            )
            db.session.add(reading)
            db.session.commit()
            process_reading(sensor, reading)
            created += 1

    return created


def prune_old_readings(retention_hours: int) -> int:
    """Delete expired readings while preserving alerts that reference them."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.models import Alert, Reading, utc_now

    if retention_hours <= 0:
        raise ValueError("retention_hours must be greater than zero")

    cutoff = utc_now() - timedelta(hours=retention_hours)
    expired_ids = select(Reading.id).where(Reading.timestamp < cutoff)
    db.session.query(Alert).filter(Alert.reading_id.in_(expired_ids)).update(
        {Alert.reading_id: None},
        synchronize_session=False,
    )
    deleted = Reading.query.filter(Reading.timestamp < cutoff).delete(
        synchronize_session=False
    )
    db.session.commit()
    logger.info("Pruned %d readings older than %d hours", deleted, retention_hours)
    return deleted


def _demo_loop(app: Flask) -> None:
    """Run the generator forever; failures are logged and retried next cycle."""
    interval = app.config["DEMO_INTERVAL_SECONDS"]
    retention_hours = app.config["DATA_RETENTION_HOURS"]
    generator = DemoReadingGenerator()
    cycle = 0

    while True:
        try:
            run_demo_cycle(app, generator)
            cycle += 1
            if cycle % _PRUNE_EVERY_CYCLES == 0:
                with app.app_context():
                    prune_old_readings(retention_hours)
        except Exception:
            logger.exception("Hosted demo reading cycle failed")
            try:
                with app.app_context():
                    db.session.rollback()
            except Exception:
                logger.exception("Could not roll back failed demo database cycle")

        threading.Event().wait(interval)


def start_demo_thread(app: Flask) -> bool:
    """Start the single process-wide daemon, unless reloader parent process."""
    global _thread_started

    if not app.config.get("DEMO_MODE"):
        return False
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        logger.info("Hosted demo thread deferred to the Werkzeug reloader child")
        return False
    if app.config["DEMO_INTERVAL_SECONDS"] <= 0:
        raise ValueError("DEMO_INTERVAL_SECONDS must be greater than zero")
    if app.config["DATA_RETENTION_HOURS"] <= 0:
        raise ValueError("DATA_RETENTION_HOURS must be greater than zero")

    with _thread_lock:
        if _thread_started:
            return False
        thread = threading.Thread(
            target=_demo_loop,
            args=(app,),
            name="smartfacility-demo",
            daemon=True,
        )
        thread.start()
        _thread_started = True
        logger.info(
            "Started hosted demo sensor generator (interval=%ss)",
            app.config["DEMO_INTERVAL_SECONDS"],
        )
        return True
