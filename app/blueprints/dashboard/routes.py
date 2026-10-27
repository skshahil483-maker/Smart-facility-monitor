"""
app/blueprints/dashboard/routes.py – Web dashboard routes.

All routes require a logged-in user (enforced via @login_required).
Role-based access is enforced per route using the role_required helper.

Routes:
    GET  /                        – redirect to overview
    GET  /overview                – sensor overview cards
    GET  /sensors/<id>            – sensor detail with trend chart
    GET  /sensors/<id>/chart.png  – chart image served as PNG
    GET/POST /alerts              – paginated alerts with filters
    POST /alerts/<id>/acknowledge – engineer/admin: acknowledge alert
    POST /alerts/<id>/resolve     – engineer/admin: resolve alert
    GET/POST /admin/sensors        – admin: sensor list and create
    GET/POST /admin/sensors/<id>/edit – admin: edit sensor
    POST     /admin/sensors/<id>/delete – admin: delete sensor
    GET      /admin/users          – admin: user list
    GET/POST /admin/users/new      – admin: create user
    GET/POST /admin/users/<id>/edit – admin: edit user role
"""

import logging
from functools import wraps

from flask import (
    abort,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app import db
from app.blueprints.dashboard import dashboard_bp
from app.charts import generate_sensor_chart
from app.forms import CreateUserForm, EditUserForm, SensorForm
from app.models import Alert, Reading, Sensor, User, utc_now

logger = logging.getLogger(__name__)


# ── Role enforcement helper ───────────────────────────────────────────────────

def role_required(*roles: str):
    """
    Decorator that restricts a route to users with one of *roles*.
    Must be applied *after* @login_required.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if current_user.role not in roles:
                flash("You do not have permission to access that page.", "danger")
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ── Root redirect ─────────────────────────────────────────────────────────────

@dashboard_bp.route("/")
@login_required
def index():
    """Redirect root URL to the overview page."""
    return redirect(url_for("dashboard.overview"))


# ── Overview ──────────────────────────────────────────────────────────────────

@dashboard_bp.route("/overview")
@login_required
def overview():
    """
    Sensor overview page.

    Shows a card for every active sensor with its latest value and a
    status badge (green / yellow / red). Open-alert count shown in header.
    Page auto-refreshes every 10 seconds via HTML meta-refresh tag.
    """
    sensors = Sensor.query.filter_by(is_active=True).order_by(Sensor.location).all()
    open_alert_count = Alert.query.filter_by(status="open").count()

    # Build a list of (sensor, latest_reading, status) tuples for the template
    sensor_data = []
    for s in sensors:
        lr = s.latest_reading()
        sensor_data.append(
            {
                "sensor": s,
                "latest": lr,
                "status": s.status(),
            }
        )

    return render_template(
        "overview.html",
        title="Facility Overview",
        sensor_data=sensor_data,
        open_alert_count=open_alert_count,
        auto_refresh=10,  # seconds
    )


# ── Sensor detail ─────────────────────────────────────────────────────────────

@dashboard_bp.route("/sensors/<int:sensor_id>")
@login_required
def sensor_detail(sensor_id: int):
    """
    Sensor detail page.

    Shows:
        - Latest value and status badge
        - Embedded trend chart image (PNG served from chart route)
        - Table of the 50 most recent readings
        - Open alerts for this sensor
    """
    sensor = db.session.get(Sensor, sensor_id)
    if sensor is None:
        abort(404)

    recent_readings = (
        Reading.query.filter_by(sensor_id=sensor_id)
        .order_by(Reading.timestamp.desc(), Reading.id.desc())
        .limit(50)
        .all()
    )
    open_alerts = (
        Alert.query.filter_by(sensor_id=sensor_id, status="open")
        .order_by(Alert.created_at.desc(), Alert.id.desc())
        .all()
    )

    return render_template(
        "sensor_detail.html",
        title=sensor.name,
        sensor=sensor,
        recent_readings=recent_readings,
        open_alerts=open_alerts,
    )


@dashboard_bp.route("/sensors/<int:sensor_id>/chart.png")
@login_required
def sensor_chart(sensor_id: int):
    """
    Serve the sensor trend chart as a PNG image.

    Plots the last 100 readings with threshold lines.
    Called by an <img> tag inside sensor_detail.html.
    """
    sensor = db.session.get(Sensor, sensor_id)
    if sensor is None:
        abort(404)

    readings = (
        Reading.query.filter_by(sensor_id=sensor_id)
        .order_by(Reading.timestamp.desc(), Reading.id.desc())
        .limit(100)
        .all()
    )
    readings.reverse()

    png_bytes = generate_sensor_chart(sensor, readings)

    response = make_response(png_bytes)
    response.headers["Content-Type"] = "image/png"
    response.headers["Cache-Control"] = "no-cache, no-store"
    return response


# ── Alerts ────────────────────────────────────────────────────────────────────

@dashboard_bp.route("/alerts", methods=["GET", "POST"])
@login_required
def alerts():
    """
    Paginated alert listing with status/severity filters.

    GET:  render the alerts table with optional query-string filters.
    POST: redirect back to GET (PRG pattern after badge interactions).
    """
    status_filter = request.args.get("status", "")
    severity_filter = request.args.get("severity", "")
    page = request.args.get("page", 1, type=int)

    from flask import current_app
    per_page = current_app.config.get("ALERTS_PER_PAGE", 20)

    query = Alert.query

    VALID_STATUSES = {"open", "acknowledged", "resolved"}
    VALID_SEVERITIES = {"warning", "critical", "drift"}

    if status_filter and status_filter in VALID_STATUSES:
        query = query.filter_by(status=status_filter)
    if severity_filter and severity_filter in VALID_SEVERITIES:
        query = query.filter_by(severity=severity_filter)

    pagination = (
        query.order_by(Alert.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return render_template(
        "alerts.html",
        title="Alerts",
        pagination=pagination,
        alerts=pagination.items,
        status_filter=status_filter,
        severity_filter=severity_filter,
    )


@dashboard_bp.route("/alerts/<int:alert_id>/acknowledge", methods=["POST"])
@login_required
@role_required("engineer", "admin")
def acknowledge_alert(alert_id: int):
    """Mark an alert as acknowledged. Engineers and admins only."""
    alert = db.session.get(Alert, alert_id)
    if alert is None:
        abort(404)
    if alert.status != "open":
        flash("Only open alerts can be acknowledged.", "warning")
    else:
        alert.status = "acknowledged"
        alert.acknowledged_by = current_user.id
        db.session.commit()
        flash(f"Alert #{alert_id} acknowledged.", "success")

    return redirect(url_for("dashboard.alerts"))


@dashboard_bp.route("/alerts/<int:alert_id>/resolve", methods=["POST"])
@login_required
@role_required("engineer", "admin")
def resolve_alert(alert_id: int):
    """Mark an alert as resolved. Engineers and admins only."""
    alert = db.session.get(Alert, alert_id)
    if alert is None:
        abort(404)
    if alert.status == "resolved":
        flash("Alert is already resolved.", "warning")
    else:
        alert.status = "resolved"
        alert.resolved_by = current_user.id
        alert.resolved_at = utc_now()
        db.session.commit()
        flash(f"Alert #{alert_id} resolved.", "success")

    return redirect(url_for("dashboard.alerts"))


# ── Admin: Sensor CRUD ────────────────────────────────────────────────────────

@dashboard_bp.route("/admin/sensors", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin_sensors():
    """Admin: list sensors and create a new one."""
    form = SensorForm()
    if form.validate_on_submit():
        if Sensor.query.filter_by(name=form.name.data).first():
            flash("A sensor with that name already exists.", "warning")
        else:
            sensor = Sensor(
                name=form.name.data,
                type=form.type.data,
                location=form.location.data,
                unit=form.unit.data,
                warning_min=form.warning_min.data,
                warning_max=form.warning_max.data,
                critical_min=form.critical_min.data,
                critical_max=form.critical_max.data,
                is_active=form.is_active.data,
            )
            db.session.add(sensor)
            db.session.commit()
            flash(f"Sensor '{sensor.name}' created.", "success")
            return redirect(url_for("dashboard.admin_sensors"))

    sensors = Sensor.query.order_by(Sensor.location, Sensor.name).all()
    return render_template(
        "admin/sensors.html",
        title="Manage Sensors",
        sensors=sensors,
        form=form,
    )


@dashboard_bp.route("/admin/sensors/<int:sensor_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_sensor(sensor_id: int):
    """Admin: edit an existing sensor."""
    sensor = db.session.get(Sensor, sensor_id)
    if sensor is None:
        abort(404)

    form = SensorForm(obj=sensor)
    if form.validate_on_submit():
        # Check uniqueness (ignore the current sensor)
        existing = Sensor.query.filter_by(name=form.name.data).first()
        if existing and existing.id != sensor_id:
            flash("Another sensor with that name already exists.", "warning")
        else:
            form.populate_obj(sensor)
            db.session.commit()
            flash(f"Sensor '{sensor.name}' updated.", "success")
            return redirect(url_for("dashboard.admin_sensors"))

    return render_template(
        "admin/edit_sensor.html",
        title="Edit Sensor",
        sensor=sensor,
        form=form,
    )


@dashboard_bp.route("/admin/sensors/<int:sensor_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_sensor(sensor_id: int):
    """Admin: delete a sensor and its readings/alerts (cascade)."""
    sensor = db.session.get(Sensor, sensor_id)
    if sensor is None:
        abort(404)
    name = sensor.name
    db.session.delete(sensor)
    db.session.commit()
    flash(f"Sensor '{name}' and all its data deleted.", "success")
    return redirect(url_for("dashboard.admin_sensors"))


# ── Admin: User management ────────────────────────────────────────────────────

@dashboard_bp.route("/admin/users")
@login_required
@role_required("admin")
def admin_users():
    """Admin: list all user accounts."""
    users = User.query.order_by(User.username).all()
    return render_template("admin/users.html", title="Manage Users", users=users)


@dashboard_bp.route("/admin/users/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
def create_user():
    """Admin: create a new user account."""
    form = CreateUserForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            role=form.role.data,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f"User '{user.username}' created.", "success")
        return redirect(url_for("dashboard.admin_users"))

    return render_template("admin/create_user.html", title="Create User", form=form)


@dashboard_bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_user(user_id: int):
    """Admin: edit a user's role and active status."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    form = EditUserForm(obj=user)
    if form.validate_on_submit():
        removing_last_active_admin = (
            user.role == "admin"
            and user.is_active
            and (form.role.data != "admin" or not form.is_active.data)
            and User.query.filter_by(role="admin", is_active=True).count() <= 1
        )
        if removing_last_active_admin:
            flash("At least one active administrator must remain.", "danger")
            return render_template(
                "admin/edit_user.html",
                title="Edit User",
                target_user=user,
                form=form,
            )

        user.role = form.role.data
        user.is_active = form.is_active.data
        db.session.commit()
        flash(f"User '{user.username}' updated.", "success")
        return redirect(url_for("dashboard.admin_users"))

    return render_template(
        "admin/edit_user.html", title="Edit User", target_user=user, form=form
    )
