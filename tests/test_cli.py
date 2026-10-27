"""Tests for database initialization and sample-data CLI commands."""

from app.models import Sensor, User


def test_init_db_and_seed_commands(app):
    runner = app.test_cli_runner()

    initialized = runner.invoke(args=["init-db"])
    assert initialized.exit_code == 0

    seeded = runner.invoke(args=["seed"])
    assert seeded.exit_code == 0

    with app.app_context():
        admin = User.query.filter_by(username="admin").one()
        assert admin.role == "admin"
        assert admin.must_change_password is True
        assert admin.check_password("Admin@123")
        seeded_names = {
            "Boiler Temperature",
            "Compressor Pressure",
            "HVAC Humidity",
            "CO₂ Gas Level",
            "Motor Vibration",
            "Chiller Temperature",
        }
        assert Sensor.query.filter(Sensor.name.in_(seeded_names)).count() == 6
