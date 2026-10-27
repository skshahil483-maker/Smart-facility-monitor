"""
app/forms.py – WTForms form definitions.

All forms use FlaskForm (which adds automatic CSRF protection).
"""

import math

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    EmailField,
    FloatField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)


# ── Auth forms ────────────────────────────────────────────────────────────────

class LoginForm(FlaskForm):
    """Login form: username + password."""

    username = StringField(
        "Username",
        validators=[DataRequired(), Length(min=2, max=64)],
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired()],
    )
    remember_me = BooleanField("Remember me")
    submit = SubmitField("Sign In")


class ChangePasswordForm(FlaskForm):
    """Allow users to change their own password."""

    current_password = PasswordField("Current Password", validators=[DataRequired()])
    new_password = PasswordField(
        "New Password",
        validators=[DataRequired(), Length(min=8, max=128)],
    )
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )
    submit = SubmitField("Change Password")


# ── Sensor forms ──────────────────────────────────────────────────────────────

class SensorForm(FlaskForm):
    """Create or edit a sensor."""

    SENSOR_TYPES = [
        ("temperature", "Temperature"),
        ("pressure", "Pressure"),
        ("humidity", "Humidity"),
        ("gas", "Gas"),
        ("vibration", "Vibration"),
    ]

    name = StringField(
        "Sensor Name",
        validators=[DataRequired(), Length(min=2, max=120)],
    )
    type = SelectField(
        "Type",
        choices=SENSOR_TYPES,
        validators=[DataRequired()],
    )
    location = StringField(
        "Location",
        validators=[DataRequired(), Length(min=2, max=120)],
    )
    unit = StringField(
        "Unit (e.g. °C, bar, %RH)",
        validators=[DataRequired(), Length(min=1, max=20)],
    )
    warning_min = FloatField("Warning Min", validators=[Optional()])
    warning_max = FloatField("Warning Max", validators=[Optional()])
    critical_min = FloatField("Critical Min", validators=[Optional()])
    critical_max = FloatField("Critical Max", validators=[Optional()])
    is_active = BooleanField("Active", default=True)
    submit = SubmitField("Save Sensor")

    def validate(self, extra_validators=None) -> bool:
        """Validate finite thresholds and sensible warning/critical ranges."""
        valid = super().validate(extra_validators=extra_validators)

        threshold_fields = (
            self.warning_min,
            self.warning_max,
            self.critical_min,
            self.critical_max,
        )
        for field in threshold_fields:
            if field.data is not None and not math.isfinite(field.data):
                field.errors.append("Thresholds must be finite numbers.")
                valid = False

        pairs = (
            (self.warning_min, self.warning_max, "Warning minimum must be below maximum."),
            (self.critical_min, self.critical_max, "Critical minimum must be below maximum."),
        )
        for lower, upper, message in pairs:
            if lower.data is not None and upper.data is not None and lower.data >= upper.data:
                upper.errors.append(message)
                valid = False

        if (
            self.critical_min.data is not None
            and self.warning_min.data is not None
            and self.critical_min.data > self.warning_min.data
        ):
            self.critical_min.errors.append(
                "Critical minimum must not exceed warning minimum."
            )
            valid = False
        if (
            self.critical_max.data is not None
            and self.warning_max.data is not None
            and self.critical_max.data < self.warning_max.data
        ):
            self.critical_max.errors.append(
                "Critical maximum must not be below warning maximum."
            )
            valid = False
        return valid


# ── User management forms ─────────────────────────────────────────────────────

class CreateUserForm(FlaskForm):
    """Admin form: create a new user account."""

    ROLES = [
        ("viewer", "Viewer (read-only)"),
        ("engineer", "Engineer (acknowledge/resolve alerts)"),
        ("admin", "Admin (full access)"),
    ]

    username = StringField(
        "Username",
        validators=[DataRequired(), Length(min=2, max=64)],
    )
    email = EmailField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=120)],
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=8, max=128)],
    )
    role = SelectField("Role", choices=ROLES, validators=[DataRequired()])
    submit = SubmitField("Create User")

    def validate_username(self, field: StringField) -> None:
        """Ensure the username is not already taken."""
        from app.models import User
        if User.query.filter_by(username=field.data).first():
            raise ValidationError("Username already in use.")

    def validate_email(self, field: EmailField) -> None:
        """Ensure the email is not already registered."""
        from app.models import User
        if User.query.filter_by(email=field.data).first():
            raise ValidationError("Email already registered.")


class EditUserForm(FlaskForm):
    """Admin form: edit an existing user's role or status."""

    ROLES = [
        ("viewer", "Viewer"),
        ("engineer", "Engineer"),
        ("admin", "Admin"),
    ]

    role = SelectField("Role", choices=ROLES, validators=[DataRequired()])
    is_active = BooleanField("Account Active")
    submit = SubmitField("Save Changes")
