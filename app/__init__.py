"""
app/__init__.py – Application factory.

Creates and configures the Flask app, registers extensions,
blueprints, CLI commands, logging, and error handlers.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

import click
from flask import Flask, jsonify, request
from flask_login import LoginManager, current_user
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.exceptions import HTTPException

from config import config_map

# ── Extension instances (created here, initialised in create_app) ────────────
db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(config_name: str = "development") -> Flask:
    """
    Application factory.

    Args:
        config_name: One of 'development', 'testing', 'production'.

    Returns:
        A fully configured Flask application instance.
    """
    app = Flask(__name__)

    # ── Load configuration ────────────────────────────────────────────────────
    cfg = config_map.get(config_name, config_map["default"])
    app.config.from_object(cfg)
    if config_name == "production":
        missing_settings = [
            setting
            for setting in ("SECRET_KEY", "API_KEY")
            if not app.config.get(setting)
        ]
        if missing_settings:
            raise RuntimeError(
                "Production requires environment settings: "
                + ", ".join(missing_settings)
            )

    # ── Initialise extensions ─────────────────────────────────────────────────
    db.init_app(app)
    csrf.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"          # redirect to login page
    login_manager.login_message_category = "warning"

    # ── Register blueprints ───────────────────────────────────────────────────
    from app.blueprints.auth import auth_bp
    from app.blueprints.api import api_bp
    from app.blueprints.dashboard import dashboard_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(dashboard_bp)

    @app.before_request
    def require_password_change():
        """Limit first-login users to the password update and logout pages."""
        if (
            current_user.is_authenticated
            and current_user.must_change_password
            and request.endpoint not in {"auth.change_password", "auth.logout"}
        ):
            from flask import redirect, url_for

            return redirect(url_for("auth.change_password"))
        return None

    # ── Health check endpoint ─────────────────────────────────────────────────
    @app.route("/health")
    def health():
        """Simple health check – returns 200 OK with JSON."""
        return jsonify({"status": "ok", "version": "1.0.0"}), 200

    # ── Global JSON error handler for /api/* ─────────────────────────────────
    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({"error": exc.description}), exc.code
        return exc

    @app.errorhandler(500)
    def internal_error(exc):
        db.session.rollback()
        app.logger.error("Unhandled application error: %s", exc)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500
        return exc

    # ── Logging ───────────────────────────────────────────────────────────────
    _configure_logging(app)

    # ── CLI commands ──────────────────────────────────────────────────────────
    _register_cli(app)

    from app.demo import start_demo_thread

    start_demo_thread(app)

    return app


# ── Helpers ───────────────────────────────────────────────────────────────────

def _configure_logging(app: Flask) -> None:
    """Set up rotating file + console logging."""
    level = getattr(logging, app.config.get("LOG_LEVEL", "INFO"), logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(level)

    # File handler (only outside testing to keep test output clean)
    if not app.config.get("TESTING"):
        logs_dir = os.path.join(app.root_path, "..", "logs")
        os.makedirs(logs_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(logs_dir, "smartfacility.log"),
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(level)
        app.logger.addHandler(file_handler)

    app.logger.addHandler(console)
    app.logger.setLevel(level)


def _register_cli(app: Flask) -> None:
    """Register custom Flask CLI commands."""

    @app.cli.command("init-db")
    def init_db():
        """Create all database tables."""
        db.create_all()
        click.echo("[OK] Database tables created.")

    @app.cli.command("seed")
    def seed():
        """Seed an admin user and 6 sample sensors."""
        from app.models import User, Sensor
        from werkzeug.security import generate_password_hash

        # Admin user
        if not User.query.filter_by(username="admin").first():
            admin = User(
                username="admin",
                email="admin@smartfacility.local",
                password_hash=generate_password_hash("Admin@123"),
                role="admin",
                must_change_password=True,
            )
            db.session.add(admin)
            click.echo("  + admin user created (password: Admin@123)")
        else:
            click.echo("  - admin user already exists, skipped.")

        # Sample sensors
        sensors = [
            dict(name="Boiler Temperature", type="temperature",
                 location="Boiler Room", unit="°C",
                 warning_min=60.0, warning_max=90.0,
                 critical_min=50.0, critical_max=110.0),
            dict(name="Compressor Pressure", type="pressure",
                 location="Machine Room A", unit="bar",
                 warning_min=4.0, warning_max=8.0,
                 critical_min=2.0, critical_max=10.0),
            dict(name="HVAC Humidity", type="humidity",
                 location="Server Room", unit="%RH",
                 warning_min=35.0, warning_max=65.0,
                 critical_min=20.0, critical_max=80.0),
            dict(name="CO₂ Gas Level", type="gas",
                 location="Production Floor", unit="ppm",
                 warning_min=None, warning_max=1000.0,
                 critical_min=None, critical_max=2000.0),
            dict(name="Motor Vibration", type="vibration",
                 location="Pump Station", unit="mm/s",
                 warning_min=None, warning_max=4.5,
                 critical_min=None, critical_max=7.1),
            dict(name="Chiller Temperature", type="temperature",
                 location="Cooling Tower", unit="°C",
                 warning_min=5.0, warning_max=15.0,
                 critical_min=2.0, critical_max=20.0),
        ]

        added = 0
        for s in sensors:
            if not Sensor.query.filter_by(name=s["name"]).first():
                db.session.add(Sensor(**s))
                added += 1

        db.session.commit()
        click.echo(f"  + {added} sensor(s) seeded.")
        click.echo("[OK] Seed complete.")
