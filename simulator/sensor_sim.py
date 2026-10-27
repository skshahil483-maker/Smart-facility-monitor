"""
simulator/sensor_sim.py – Sensor data simulator CLI script.

Posts realistic readings for all active sensors to the API at regular
intervals. Supports random noise, occasional spikes, and a gradual
drift mode for testing the drift detection rules.

Usage examples:
    # Normal simulation, every 5 seconds
    python simulator/sensor_sim.py --interval 5

    # Drift mode (gradual upward drift on all sensors)
    python simulator/sensor_sim.py --interval 3 --drift

    # Only post to a specific sensor by id
    python simulator/sensor_sim.py --sensor-id 1 --interval 2

    # Spike rate 20% (1 in 5 readings is a spike)
    python simulator/sensor_sim.py --spike-rate 0.2
"""

import argparse
import math
import os
import random
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Default config ────────────────────────────────────────────────────────────
DEFAULT_BASE_URL = "http://127.0.0.1:5000"
DEFAULT_API_KEY = os.environ.get("API_KEY", "")
DEFAULT_INTERVAL = 5      # seconds between batches
DEFAULT_SPIKE_RATE = 0.05  # 5% chance of a spike per reading
DRIFT_INCREMENT = 0.005    # fraction of warning_max added each round in drift mode


# ── Sensor baseline values (mid-range for normal operation) ──────────────────
# These match the sensors created by `flask seed`.
SENSOR_BASELINES = {
    "temperature": {"base": 75.0, "noise": 2.0},  # Boiler: warn=90, crit=110
    "pressure":    {"base": 6.0,  "noise": 0.3},   # Compressor: warn=8, crit=10
    "humidity":    {"base": 50.0, "noise": 3.0},   # HVAC: warn=65, crit=80
    "gas":         {"base": 400.0,"noise": 50.0},  # CO2: warn=1000, crit=2000
    "vibration":   {"base": 2.0,  "noise": 0.4},   # Motor: warn=4.5, crit=7.1
}


class SensorSimulator:
    """
    Sends realistic sensor readings to the SmartFacility Monitor API.

    Args:
        base_url:    Flask app URL (e.g. http://localhost:5000).
        api_key:     X-API-Key value.
        interval:    Seconds between each batch of readings.
        spike_rate:  Probability [0..1] of a spike on any given reading.
        drift_mode:  If True, readings slowly drift upward each round.
        sensor_id:   If set, only simulate this specific sensor id.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        interval: float,
        spike_rate: float,
        drift_mode: bool,
        sensor_id: int | None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self.interval = interval
        self.spike_rate = spike_rate
        self.drift_mode = drift_mode
        self.sensor_id = sensor_id

        # Per-sensor drift accumulators {sensor_id: drift_offset}
        self._drift: dict[int, float] = {}
        # Round counter for sinusoidal noise component
        self._round = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Main loop: fetch sensors, post readings, sleep, repeat."""
        print(f"[SIM] Starting SmartFacility sensor simulator")
        print(f"[SIM] Target: {self.base_url}  |  Interval: {self.interval}s")
        print(f"[SIM] Drift mode: {self.drift_mode}  |  Spike rate: {self.spike_rate:.0%}")
        print(f"[SIM] Press Ctrl+C to stop.\n")

        try:
            while True:
                self._round += 1
                sensors = self._fetch_sensors()
                if not sensors:
                    print("[SIM] No active sensors found. Retrying in 10s…")
                    time.sleep(10)
                    continue

                for s in sensors:
                    if self.sensor_id and s["id"] != self.sensor_id:
                        continue
                    self._post_reading(s)

                time.sleep(self.interval)

        except KeyboardInterrupt:
            print("\n[SIM] Stopped by user.")
            sys.exit(0)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _fetch_sensors(self) -> list[dict]:
        """GET /api/sensors and return the list of active sensors."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/sensors",
                headers=self.headers,
                timeout=5,
            )
            resp.raise_for_status()
            return resp.json().get("sensors", [])
        except requests.RequestException as exc:
            print(f"[SIM] ERROR fetching sensors: {exc}")
            return []

    def _generate_value(self, sensor: dict) -> float:
        """
        Generate a realistic reading for *sensor*.

        Combines:
            - Baseline value for the sensor type
            - Gaussian noise
            - Sinusoidal component (slow cycle)
            - Occasional spike (if random draw < spike_rate)
            - Gradual drift increment (if drift_mode is True)
        """
        stype = sensor.get("type", "temperature")
        params = SENSOR_BASELINES.get(stype, {"base": 50.0, "noise": 5.0})

        base: float = params["base"]
        noise: float = params["noise"]

        # Sinusoidal slow variation (period ~60 rounds)
        sine = math.sin(self._round * 2 * math.pi / 60) * noise * 0.5
        # Gaussian noise
        gaussian = random.gauss(0, noise * 0.3)

        value = base + sine + gaussian

        # Drift: slowly increase offset each round
        if self.drift_mode:
            drift_offset = self._drift.get(sensor["id"], 0.0)
            # Use warning_max as reference; fall back to base * 1.2
            ref = sensor.get("warning_max") or (base * 1.2)
            drift_offset += ref * DRIFT_INCREMENT
            self._drift[sensor["id"]] = drift_offset
            value += drift_offset

        # Spike: occasionally push the value above the warning threshold
        if random.random() < self.spike_rate:
            spike_multiplier = random.uniform(1.2, 1.6)
            warning_max = sensor.get("warning_max")
            if warning_max:
                value = warning_max * spike_multiplier
            else:
                value *= spike_multiplier
            print(f"[SIM] ⚡ SPIKE on sensor #{sensor['id']} ({sensor['name']}): {value:.2f}")

        return round(value, 3)

    def _post_reading(self, sensor: dict) -> None:
        """POST a single reading to /api/readings."""
        value = self._generate_value(sensor)
        payload = {"sensor_id": sensor["id"], "value": value}

        try:
            resp = requests.post(
                f"{self.base_url}/api/readings",
                json=payload,
                headers=self.headers,
                timeout=5,
            )
            alerts = resp.json().get("alerts_created", [])
            alert_str = f" → {len(alerts)} alert(s) created" if alerts else ""
            ts = datetime.now().strftime("%H:%M:%S")
            print(
                f"[{ts}] Sensor #{sensor['id']:2d} {sensor['name']:<30s} "
                f"= {value:8.3f} {sensor.get('unit',''):5s}  "
                f"HTTP {resp.status_code}{alert_str}"
            )
        except requests.RequestException as exc:
            print(f"[SIM] ERROR posting to sensor #{sensor['id']}: {exc}")


# ── CLI entry point ───────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="SmartFacility sensor data simulator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base URL of the Flask app",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="X-API-Key for authentication",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="Seconds between each batch of readings",
    )
    parser.add_argument(
        "--spike-rate",
        type=float,
        default=DEFAULT_SPIKE_RATE,
        help="Probability of a spike on any given reading (0.0–1.0)",
    )
    parser.add_argument(
        "--drift",
        action="store_true",
        help="Enable gradual drift mode (tests drift detection alerts)",
    )
    parser.add_argument(
        "--sensor-id",
        type=int,
        default=None,
        help="Simulate only this sensor id (default: all active sensors)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    args.api_key = args.api_key or DEFAULT_API_KEY
    if not args.api_key:
        raise SystemExit(
            "Set API_KEY in .env or pass --api-key before running the simulator."
        )
    if args.interval <= 0:
        raise SystemExit("--interval must be greater than zero.")
    if not 0 <= args.spike_rate <= 1:
        raise SystemExit("--spike-rate must be between 0 and 1.")
    if args.sensor_id is not None and args.sensor_id <= 0:
        raise SystemExit("--sensor-id must be a positive integer.")

    sim = SensorSimulator(
        base_url=args.base_url,
        api_key=args.api_key,
        interval=args.interval,
        spike_rate=args.spike_rate,
        drift_mode=args.drift,
        sensor_id=args.sensor_id,
    )
    sim.run()
