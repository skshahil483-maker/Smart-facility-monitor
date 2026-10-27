"""
app/blueprints/api/routes.py – REST API endpoints (JSON).

Authentication: X-API-Key header (all POST/PATCH routes).
CSRF is disabled for this blueprint (uses token-based auth instead).

Endpoints:
    POST   /api/readings                    – ingest a sensor reading
    GET    /api/sensors                     – list all active sensors
    GET    /api/sensors/<id>/readings       – get readings for one sensor
    GET    /api/alerts                      – list alerts (filterable)
    PATCH  /api/alerts/<id>                 – acknowledge or resolve an alert
"""

import hmac
import logging
import math
from functools import wraps
from typing import Any

from flask import current_app, jsonify, request
from flask_login import current_user

from app import csrf, db
from app.blueprints.api import api_bp
from app.models import Alert, Reading, Sensor, utc_now

logger = logging.getLogger(__name__)


# ── Auth decorators ───────────────────────────────────────────────────────────

def require_api_key(f):
    """
    Decorator: require a valid X-API-Key header.

    Returns 401 JSON if the key is missing or wrong.
    """
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any):
        key = request.headers.get("X-API-Key", "")
        configured_key = current_app.config.get("API_KEY") or ""
        if not configured_key or not hmac.compare_digest(key, configured_key):
            return jsonify({"error": "Unauthorised – invalid or missing API key."}), 401
        return f(*args, **kwargs)

    return decorated


def require_session_role(*roles: str):
    """
    Decorator: require an authenticated web session with one of *roles*.

    Used for alert updates, which require an authenticated operator session.
    Returns 403 JSON if the role is insufficient.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args: Any, **kwargs: Any):
            if not current_user.is_authenticated:
                return jsonify({"error": "Authentication required."}), 401
            if current_user.role not in roles:
                return jsonify({"error": "Insufficient privileges."}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# ── Helper ────────────────────────────────────────────────────────────────────

def _json_error(message: str, status: int) -> Any:
    """Return a consistent JSON error response."""
    return jsonify({"error": message}), status


# ── POST /api/readings ────────────────────────────────────────────────────────

@api_bp.route("/readings", methods=["POST"])
@csrf.exempt
@require_api_key
def ingest_reading():
    """
    Ingest a new sensor reading.

    Request body (JSON):
        { "sensor_id": 1, "value": 72.5 }

    Runs the rules engine after storing the reading.
    Returns 201 with the stored reading and any new alerts.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("Request body must be valid JSON.", 400)

    # Validate required fields
    sensor_id = payload.get("sensor_id")
    value = payload.get("value")

    if sensor_id is None:
        return _json_error("Missing required field: sensor_id.", 400)
    if value is None:
        return _json_error("Missing required field: value.", 400)

    # Type coercion + validation
    if isinstance(sensor_id, bool) or not isinstance(sensor_id, int):
        return _json_error("sensor_id must be an integer and value must be a number.", 400)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _json_error("sensor_id must be an integer and value must be a number.", 400)

    if sensor_id <= 0:
        return _json_error("sensor_id must be a positive integer.", 400)
    try:
        value = float(value)
    except OverflowError:
        return _json_error("value must be a finite number.", 400)
    if not math.isfinite(value):
        return _json_error("value must be a finite number.", 400)

    # Sensor must exist and be active
    sensor = db.session.get(Sensor, sensor_id)
    if sensor is None:
        return _json_error(f"Sensor with id={sensor_id} not found.", 404)
    if not sensor.is_active:
        return _json_error(f"Sensor with id={sensor_id} is inactive.", 409)

    # Persist the reading
    reading = Reading(
        sensor_id=sensor_id,
        value=value,
        timestamp=utc_now(),
    )
    db.session.add(reading)
    db.session.commit()  # commit so the reading has an id before rules engine

    # Run the rules engine
    from app.rules import process_reading
    new_alerts = process_reading(sensor, reading)

    logger.info("Reading stored: sensor=%d value=%.3f alerts=%d", sensor_id, value, len(new_alerts))

    return (
        jsonify(
            {
                "reading": reading.to_dict(),
                "alerts_created": [a.to_dict() for a in new_alerts],
            }
        ),
        201,
    )


# ── GET /api/sensors ──────────────────────────────────────────────────────────

@api_bp.route("/sensors", methods=["GET"])
def list_sensors():
    """
    Return a list of all active sensors with their latest readings.

    Query params:
        include_inactive=true – also include inactive sensors
    """
    include_inactive = request.args.get("include_inactive", "false").lower() == "true"
    query = Sensor.query
    if not include_inactive:
        query = query.filter_by(is_active=True)
    sensors = query.order_by(Sensor.name).all()
    return jsonify({"sensors": [s.to_dict() for s in sensors]}), 200


# ── GET /api/sensors/<id>/readings ────────────────────────────────────────────

@api_bp.route("/sensors/<int:sensor_id>/readings", methods=["GET"])
def get_readings(sensor_id: int):
    """
    Return recent readings for a specific sensor.

    Query params:
        limit (int, default 100) – maximum number of readings to return
    """
    sensor = db.session.get(Sensor, sensor_id)
    if sensor is None:
        return _json_error(f"Sensor {sensor_id} not found.", 404)

    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        return _json_error("limit must be a positive integer.", 400)
    if limit <= 0:
        return _json_error("limit must be a positive integer.", 400)
    limit = min(limit, 1000)

    readings = (
        Reading.query.filter_by(sensor_id=sensor_id)
        .order_by(Reading.timestamp.desc(), Reading.id.desc())
        .limit(limit)
        .all()
    )
    return (
        jsonify(
            {
                "sensor_id": sensor_id,
                "count": len(readings),
                "readings": [r.to_dict() for r in readings],
            }
        ),
        200,
    )


# ── GET /api/alerts ───────────────────────────────────────────────────────────

@api_bp.route("/alerts", methods=["GET"])
def list_alerts():
    """
    Return alerts with optional filtering.

    Query params:
        status   – 'open' | 'acknowledged' | 'resolved'
        severity – 'warning' | 'critical' | 'drift'
        limit    (default 50)
    """
    query = Alert.query

    status_filter = request.args.get("status")
    severity_filter = request.args.get("severity")

    VALID_STATUSES = {"open", "acknowledged", "resolved"}
    VALID_SEVERITIES = {"warning", "critical", "drift"}

    if status_filter:
        if status_filter not in VALID_STATUSES:
            return _json_error(f"Invalid status. Choose from: {', '.join(VALID_STATUSES)}", 400)
        query = query.filter_by(status=status_filter)

    if severity_filter:
        if severity_filter not in VALID_SEVERITIES:
            return _json_error(f"Invalid severity. Choose from: {', '.join(VALID_SEVERITIES)}", 400)
        query = query.filter_by(severity=severity_filter)

    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        return _json_error("limit must be a positive integer.", 400)
    if limit <= 0:
        return _json_error("limit must be a positive integer.", 400)
    limit = min(limit, 500)

    alerts = query.order_by(Alert.created_at.desc(), Alert.id.desc()).limit(limit).all()
    return jsonify({"count": len(alerts), "alerts": [a.to_dict() for a in alerts]}), 200


# ── PATCH /api/alerts/<id> ────────────────────────────────────────────────────

@api_bp.route("/alerts/<int:alert_id>", methods=["PATCH"])
@require_session_role("engineer", "admin")
def update_alert(alert_id: int):
    """
    Acknowledge or resolve an alert.

    Request body (JSON):
        { "action": "acknowledge" }   or   { "action": "resolve" }

    Requires an authenticated engineer or admin browser session. Unlike
    reading ingestion, this privileged action is not authorized by the shared
    ingestion API key.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("Request body must be valid JSON.", 400)

    action = payload.get("action", "")
    if not isinstance(action, str):
        return _json_error("action must be 'acknowledge' or 'resolve'.", 400)
    action = action.lower()
    if action not in ("acknowledge", "resolve"):
        return _json_error("action must be 'acknowledge' or 'resolve'.", 400)

    alert = db.session.get(Alert, alert_id)
    if alert is None:
        return _json_error(f"Alert {alert_id} not found.", 404)

    if action == "acknowledge":
        if alert.status != "open":
            return _json_error("Only open alerts can be acknowledged.", 409)
        alert.status = "acknowledged"
        alert.acknowledged_by = current_user.id

    elif action == "resolve":
        if alert.status == "resolved":
            return _json_error("Alert is already resolved.", 409)
        alert.status = "resolved"
        alert.resolved_by = current_user.id
        alert.resolved_at = utc_now()

    db.session.commit()
    return jsonify({"message": f"Alert {alert_id} {action}d.", "alert": alert.to_dict()}), 200
