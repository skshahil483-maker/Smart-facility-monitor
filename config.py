"""
config.py – Application configuration classes.

Uses python-dotenv to read .env automatically.
Switch DATABASE_URL env var between SQLite (default) and MySQL.
"""

import os
import secrets
from dotenv import load_dotenv

# Load .env file from the project root (one directory above this file)
load_dotenv()


class Config:
    """Base configuration shared by all environments."""

    # ── Security ────────────────────────────────────────────────────────────
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "")
    WTF_CSRF_ENABLED: bool = True
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    REMEMBER_COOKIE_HTTPONLY: bool = True

    # ── Database ─────────────────────────────────────────────────────────────
    # Defaults to SQLite so the project runs with zero database setup.
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "DATABASE_URL", "sqlite:///smartfacility.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    SQLALCHEMY_ECHO: bool = False  # Set True to log all SQL statements

    # ── API ──────────────────────────────────────────────────────────────────
    API_KEY: str = os.environ.get("API_KEY", "")

    # ── Pagination ───────────────────────────────────────────────────────────
    ALERTS_PER_PAGE: int = int(os.environ.get("ALERTS_PER_PAGE", 20))

    # ── Logging ──────────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")


class DevelopmentConfig(Config):
    """Development overrides."""

    SECRET_KEY: str = Config.SECRET_KEY or secrets.token_hex(32)
    DEBUG: bool = True
    SQLALCHEMY_ECHO: bool = False  # flip to True to debug queries


class TestingConfig(Config):
    """Testing overrides – in-memory SQLite, no CSRF."""

    TESTING: bool = True
    WTF_CSRF_ENABLED: bool = False
    SQLALCHEMY_DATABASE_URI: str = "sqlite:///:memory:"
    API_KEY: str = "test-api-key"
    SECRET_KEY: str = "test-secret"


class ProductionConfig(Config):
    """Production overrides – stricter settings."""

    DEBUG: bool = False
    TESTING: bool = False
    SESSION_COOKIE_SECURE: bool = True
    REMEMBER_COOKIE_SECURE: bool = True


# Map a string name → class so we can pick via FLASK_ENV
config_map: dict = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
