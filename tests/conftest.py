"""
tests/conftest.py – Pytest fixtures shared across all test modules.

Provides:
    app      – Flask app with TestingConfig (in-memory SQLite)
    client   – Flask test client
    db_ready – creates all tables and seeds minimal test data
    api_headers – X-API-Key header dict for API requests
"""

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db as _db
from app.models import Alert, Reading, Sensor, User


@pytest.fixture(scope="session")
def app():
    """Create application with TestingConfig for the entire test session."""
    flask_app = create_app("testing")
    yield flask_app

    with flask_app.app_context():
        _db.session.remove()
        _db.drop_all()


@pytest.fixture(autouse=True)
def reset_database(app):
    """Give each test an isolated schema and a fresh set of fixture records."""
    with app.app_context():
        _db.drop_all()
        _db.create_all()
        _seed_test_data()


def _seed_test_data() -> None:
    """Insert minimal users and sensors needed by tests."""
    # Users
    admin = User(
        username="test_admin",
        email="admin@test.local",
        password_hash=generate_password_hash("Admin@123"),
        role="admin",
    )
    engineer = User(
        username="test_engineer",
        email="eng@test.local",
        password_hash=generate_password_hash("Eng@12345"),
        role="engineer",
    )
    viewer = User(
        username="test_viewer",
        email="viewer@test.local",
        password_hash=generate_password_hash("View@1234"),
        role="viewer",
    )

    # Sensor with thresholds
    sensor = Sensor(
        name="Test Temperature Sensor",
        type="temperature",
        location="Test Lab",
        unit="°C",
        warning_min=60.0,
        warning_max=90.0,
        critical_min=50.0,
        critical_max=110.0,
        is_active=True,
    )
    sensor_inactive = Sensor(
        name="Inactive Sensor",
        type="pressure",
        location="Store Room",
        unit="bar",
        warning_min=None,
        warning_max=8.0,
        critical_min=None,
        critical_max=10.0,
        is_active=False,
    )

    _db.session.add_all([admin, engineer, viewer, sensor, sensor_inactive])
    _db.session.commit()


@pytest.fixture()
def client(app):
    """Return a Flask test client."""
    return app.test_client()


@pytest.fixture()
def api_headers(app):
    """Return HTTP headers with the test API key."""
    return {
        "X-API-Key": app.config["API_KEY"],
        "Content-Type": "application/json",
    }


@pytest.fixture()
def admin_client(client):
    """Return a test client already logged in as admin."""
    client.post(
        "/login",
        data={"username": "test_admin", "password": "Admin@123", "csrf_token": ""},
        follow_redirects=True,
    )
    return client


@pytest.fixture()
def engineer_client(client):
    """Return a test client already logged in as engineer."""
    client.post(
        "/login",
        data={"username": "test_engineer", "password": "Eng@12345", "csrf_token": ""},
        follow_redirects=True,
    )
    return client


@pytest.fixture()
def viewer_client(client):
    """Return a test client already logged in as viewer."""
    client.post(
        "/login",
        data={"username": "test_viewer", "password": "View@1234", "csrf_token": ""},
        follow_redirects=True,
    )
    return client
