"""Tests for dashboard rendering and web-role restrictions."""

from app import db
from app.models import Alert, Reading, Sensor


def test_overview_displays_sensors_and_refresh(admin_client):
    response = admin_client.get("/overview")
    assert response.status_code == 200
    assert b"Test Temperature Sensor" in response.data
    assert b"Inactive Sensor" not in response.data
    assert b'<meta http-equiv="refresh" content="10"' in response.data


def test_sensor_detail_and_chart_render(admin_client, app):
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").one()
        reading = Reading(sensor_id=sensor.id, value=75.0)
        db.session.add(reading)
        db.session.commit()
        sensor_id = sensor.id

    detail = admin_client.get(f"/sensors/{sensor_id}")
    chart = admin_client.get(f"/sensors/{sensor_id}/chart.png")
    assert detail.status_code == 200
    assert b"Recent Readings" in detail.data
    assert chart.status_code == 200
    assert chart.mimetype == "image/png"
    assert chart.data.startswith(b"\x89PNG\r\n\x1a\n")


def test_viewer_cannot_acknowledge_alert(viewer_client, app):
    with app.app_context():
        sensor = Sensor.query.filter_by(name="Test Temperature Sensor").one()
        alert = Alert(
            sensor_id=sensor.id,
            severity="warning",
            message="Role restriction check",
            status="open",
        )
        db.session.add(alert)
        db.session.commit()
        alert_id = alert.id

    response = viewer_client.post(f"/alerts/{alert_id}/acknowledge")
    assert response.status_code == 403
