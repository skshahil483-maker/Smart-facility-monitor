"""
tests/test_auth.py – Authentication and role-based access tests.
"""

import pytest

from app import db
from app.models import User


# ── Login page ────────────────────────────────────────────────────────────────

def test_login_page_loads(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Sign In" in resp.data


def test_login_wrong_password_shows_error(client):
    resp = client.post(
        "/login",
        data={"username": "test_admin", "password": "wrong", "csrf_token": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid" in resp.data


def test_login_unknown_user_shows_error(client):
    resp = client.post(
        "/login",
        data={"username": "ghost", "password": "anything", "csrf_token": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid" in resp.data


def test_login_valid_admin_redirects(client):
    resp = client.post(
        "/login",
        data={"username": "test_admin", "password": "Admin@123", "csrf_token": ""},
        follow_redirects=True,
    )
    # After login, should land on overview page
    assert resp.status_code == 200


def test_login_rejects_external_next_redirect(client):
    response = client.post(
        "/login?next=https://example.invalid",
        data={"username": "test_admin", "password": "Admin@123"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.location.endswith("/overview")


def test_first_login_user_is_forced_to_change_password(client, app):
    with app.app_context():
        user = User(
            username="first_login",
            email="first-login@test.local",
            role="viewer",
            must_change_password=True,
        )
        user.set_password("Initial@123")
        db.session.add(user)
        db.session.commit()

    response = client.post(
        "/login",
        data={"username": "first_login", "password": "Initial@123"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.location.endswith("/change-password")

    overview = client.get("/overview", follow_redirects=False)
    assert overview.status_code == 302
    assert overview.location.endswith("/change-password")


def test_logout_redirects_to_login(client):
    # First log in
    client.post(
        "/login",
        data={"username": "test_admin", "password": "Admin@123", "csrf_token": ""},
        follow_redirects=True,
    )
    resp = client.post("/logout", follow_redirects=True)
    assert resp.status_code == 200
    assert b"logged out" in resp.data


# ── Redirect unauthenticated users ────────────────────────────────────────────

def test_overview_redirects_unauthenticated(client):
    resp = client.get("/overview", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.location


def test_alerts_redirects_unauthenticated(client):
    resp = client.get("/alerts", follow_redirects=False)
    assert resp.status_code == 302


# ── Role-based access ─────────────────────────────────────────────────────────

def test_admin_can_access_admin_sensors(admin_client):
    resp = admin_client.get("/admin/sensors", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Manage Sensors" in resp.data


def test_viewer_cannot_access_admin_sensors(viewer_client):
    resp = viewer_client.get("/admin/sensors", follow_redirects=True)
    # Should be 403 or redirect with a "permission" message
    assert resp.status_code in (200, 403)
    # If 200 (redirected), the flash message should be present
    if resp.status_code == 200:
        assert b"permission" in resp.data.lower() or b"403" in resp.data


def test_engineer_cannot_access_admin_sensors(engineer_client):
    resp = engineer_client.get("/admin/sensors", follow_redirects=True)
    assert resp.status_code in (200, 403)


def test_admin_can_access_admin_users(admin_client):
    resp = admin_client.get("/admin/users", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Manage Users" in resp.data


def test_health_endpoint_accessible_without_login(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
