"""
tests/test_api.py – API blueprint tests.

Tests authentication, input validation, happy paths, and
role-based alert actions via the REST API.
"""

import json

import pytest

from app import db
from app.models import Alert, Reading, Sensor


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"


# ── POST /api/readings – authentication ──────────────────────────────────────

def test_post_reading_without_api_key_returns_401(client):
    resp = client.post(
        "/api/readings",
        json={"sensor_id": 1, "value": 75.0},
    )
    assert resp.status_code == 401


def test_post_reading_with_wrong_api_key_returns_401(client):
    resp = client.post(
        "/api/readings",
        json={"sensor_id": 1, "value": 75.0},
        headers={"X-API-Key": "wrong-key", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


# ── POST /api/readings – validation ──────────────────────────────────────────

def test_post_reading_missing_sensor_id_returns_400(client, api_headers):
    resp = client.post("/api/readings", json={"value": 75.0}, headers=api_headers)
    assert resp.status_code == 400
    assert "sensor_id" in resp.get_json()["error"]


def test_post_reading_missing_value_returns_400(client, api_headers):
    resp = client.post("/api/readings", json={"sensor_id": 1}, headers=api_headers)
    assert resp.status_code == 400
    assert "value" in resp.get_json()["error"]


def test_post_reading_non_numeric_value_returns_400(client, api_headers):
    resp = client.post(
        "/api/readings",
        json={"sensor_id": 1, "value": "not-a-number"},
        headers=api_headers,
    )
    assert resp.status_code == 400


def test_post_reading_rejects_non_finite_value(client, api_headers):
    response = client.post(
        "/api/readings",
        json={"sensor_id": 1, "value": float("nan")},
        headers=api_headers,
    )
    assert response.status_code == 400


def test_post_reading_requires_integer_sensor_id(client, api_headers):
    response = client.post(
        "/api/readings",
        json={"sensor_id": 1.5, "value": 75.0},
        headers=api_headers,
    )
    assert response.status_code == 400


def test_api_http_errors_are_json(client):
    missing_route = client.get("/api/not-a-route")
    unsupported_method = client.patch("/api/sensors")
    assert missing_route.status_code == 404
    assert missing_route.is_json
    assert unsupported_method.status_code == 405
    assert unsupported_method.is_json


def test_post_reading_unknown_sensor_returns_404(client, api_headers):
    resp = client.post(
        "/api/readings",
        json={"sensor_id": 9999, "value": 75.0},
        headers=api_headers,
    )
    assert resp.status_code == 404


def test_post_reading_inactive_sensor_returns_409(client, api_headers, app):
    """Posting to an inactive sensor should return 409 Conflict."""
    with app.app_context():
        inactive = Sensor.query.filter_by(name="Inactive Sensor").first()
        assert inactive is not None
        sensor_id = inactive.id

    resp = client.post(
        "/api/readings",
        json={"sensor_id": sensor_id, "value": 5.0},
        headers=api_headers,
    )
    assert resp.status_code == 409


def test_post_reading_no_json_body_returns_400(client, api_headers):
    resp = client.post(
        "/api/readings",
        data="not-json",
        headers=api_headers,
    )
    assert resp.status_code == 400


# ── POST /api/readings – happy path ──────────────────────────────────────────

def test_post_reading_happy_path_returns_201(client, api_headers, app):
    """A valid reading should be stored and return 201 with the reading dict."""
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
        sensor_id = sensor.id

    resp = client.post(
        "/api/readings",
        json={"sensor_id": sensor_id, "value": 75.0},
        headers=api_headers,
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert "reading" in data
    assert data["reading"]["value"] == 75.0
    assert data["reading"]["sensor_id"] == sensor_id
    assert "alerts_created" in data


def test_post_reading_critical_value_creates_alert(client, api_headers, app):
    """A value above critical_max should create an alert in the response."""
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
        sensor_id = sensor.id
        # Clear any existing critical alerts
        Alert.query.filter_by(
            sensor_id=sensor_id, severity="critical", status="open"
        ).update({"status": "resolved"})
        db.session.commit()

    resp = client.post(
        "/api/readings",
        json={"sensor_id": sensor_id, "value": 120.0},  # above critical_max=110
        headers=api_headers,
    )
    assert resp.status_code == 201
    data = resp.get_json()
    severities = [a["severity"] for a in data["alerts_created"]]
    assert "critical" in severities


# ── GET /api/sensors ──────────────────────────────────────────────────────────

def test_get_sensors_returns_list(client):
    resp = client.get("/api/sensors")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "sensors" in data
    assert isinstance(data["sensors"], list)


def test_get_sensors_excludes_inactive_by_default(client, app):
    resp = client.get("/api/sensors")
    sensors = resp.get_json()["sensors"]
    names = [s["name"] for s in sensors]
    assert "Inactive Sensor" not in names


def test_get_sensors_include_inactive_flag(client):
    resp = client.get("/api/sensors?include_inactive=true")
    sensors = resp.get_json()["sensors"]
    names = [s["name"] for s in sensors]
    assert "Inactive Sensor" in names


# ── GET /api/sensors/<id>/readings ───────────────────────────────────────────

def test_get_readings_for_valid_sensor(client, api_headers, app):
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
        sensor_id = sensor.id

    resp = client.get(f"/api/sensors/{sensor_id}/readings?limit=5")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "readings" in data
    assert data["count"] <= 5


def test_get_readings_unknown_sensor_returns_404(client):
    resp = client.get("/api/sensors/9999/readings")
    assert resp.status_code == 404


def test_get_readings_invalid_limit_returns_400(client, app):
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
        sensor_id = sensor.id

    resp = client.get(f"/api/sensors/{sensor_id}/readings?limit=not_a_number")
    assert resp.status_code == 400


def test_get_readings_non_positive_limit_returns_400(client, app):
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
        sensor_id = sensor.id

    response = client.get(f"/api/sensors/{sensor_id}/readings?limit=0")
    assert response.status_code == 400


# ── GET /api/alerts ───────────────────────────────────────────────────────────

def test_get_alerts_returns_list(client):
    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "alerts" in data


def test_get_alerts_invalid_status_returns_400(client):
    resp = client.get("/api/alerts?status=invalid_status")
    assert resp.status_code == 400


def test_get_alerts_filter_by_status(client, api_headers, app):
    resp = client.get("/api/alerts?status=open")
    assert resp.status_code == 200
    data = resp.get_json()
    for a in data["alerts"]:
        assert a["status"] == "open"


# ── PATCH /api/alerts/<id> ────────────────────────────────────────────────────

def test_patch_alert_without_api_key_returns_401(client, app):
    with app.app_context():
        # Create a test alert
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
        reading = Reading(sensor_id=sensor.id, value=95.0)
        db.session.add(reading)
        db.session.commit()
        alert = Alert(
            sensor_id=sensor.id,
            reading_id=reading.id,
            severity="warning",
            message="Test alert",
            status="open",
        )
        db.session.add(alert)
        db.session.commit()
        alert_id = alert.id

    resp = client.patch(
        f"/api/alerts/{alert_id}",
        json={"action": "acknowledge"},
        content_type="application/json",
    )
    assert resp.status_code == 401


def test_patch_alert_acknowledge(engineer_client, app):
    """Acknowledging an open alert should transition it to 'acknowledged'."""
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
        reading = Reading(sensor_id=sensor.id, value=95.0)
        db.session.add(reading)
        db.session.commit()
        alert = Alert(
            sensor_id=sensor.id,
            reading_id=reading.id,
            severity="warning",
            message="Test ack alert",
            status="open",
        )
        db.session.add(alert)
        db.session.commit()
        alert_id = alert.id

    resp = engineer_client.patch(
        f"/api/alerts/{alert_id}",
        json={"action": "acknowledge"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["alert"]["status"] == "acknowledged"


def test_patch_alert_resolve(engineer_client, app):
    """Resolving an alert should transition it to 'resolved'."""
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
        reading = Reading(sensor_id=sensor.id, value=95.0)
        db.session.add(reading)
        db.session.commit()
        alert = Alert(
            sensor_id=sensor.id,
            reading_id=reading.id,
            severity="warning",
            message="Test resolve alert",
            status="open",
        )
        db.session.add(alert)
        db.session.commit()
        alert_id = alert.id

    resp = engineer_client.patch(
        f"/api/alerts/{alert_id}",
        json={"action": "resolve"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["alert"]["status"] == "resolved"


def test_patch_alert_invalid_action_returns_400(engineer_client, app):
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
        reading = Reading(sensor_id=sensor.id, value=95.0)
        db.session.add(reading)
        db.session.commit()
        alert = Alert(
            sensor_id=sensor.id,
            reading_id=reading.id,
            severity="warning",
            message="Test invalid action",
            status="open",
        )
        db.session.add(alert)
        db.session.commit()
        alert_id = alert.id

    resp = engineer_client.patch(
        f"/api/alerts/{alert_id}",
        json={"action": "delete"},
    )
    assert resp.status_code == 400


def test_patch_alert_not_found_returns_404(engineer_client):
    resp = engineer_client.patch(
        "/api/alerts/99999",
        json={"action": "resolve"},
    )
    assert resp.status_code == 404


def test_viewer_cannot_patch_alert(viewer_client, app):
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
        alert = Alert(
            sensor_id=sensor.id,
            severity="warning",
            message="Viewer access check",
            status="open",
        )
        db.session.add(alert)
        db.session.commit()
        alert_id = alert.id

    response = viewer_client.patch(
        f"/api/alerts/{alert_id}",
        json={"action": "acknowledge"},
    )
    assert response.status_code == 403


def test_patch_alert_rejects_api_key_without_role_session(client, api_headers, app):
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").first()
        alert = Alert(
            sensor_id=sensor.id,
            severity="warning",
            message="API key access check",
            status="open",
        )
        db.session.add(alert)
        db.session.commit()
        alert_id = alert.id

    response = client.patch(
        f"/api/alerts/{alert_id}",
        json={"action": "acknowledge"},
        headers=api_headers,
    )
    assert response.status_code == 401
