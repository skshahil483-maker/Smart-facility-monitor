"""
app/blueprints/auth/routes.py – Authentication routes.

Routes:
    GET/POST /login  – show login form / authenticate
    GET      /logout – end the session
    GET/POST /change-password – force/optional password change
"""

from urllib.parse import urlsplit

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import db
from app.blueprints.auth import auth_bp
from app.forms import ChangePasswordForm, LoginForm
from app.models import User


def _is_safe_redirect(target: str | None) -> bool:
    """Reject external and scheme-relative redirect targets."""
    if not target or "\\" in target:
        return False
    parsed = urlsplit(target)
    return not parsed.scheme and not parsed.netloc and target.startswith("/")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    Show and process the login form.

    On GET:  render login.html.
    On POST: validate credentials; redirect to dashboard or next page.
    """
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.overview"))

    form = LoginForm()
    if form.validate_on_submit():
        user: User | None = User.query.filter_by(username=form.username.data).first()

        if user is None or not user.check_password(form.password.data):
            flash("Invalid username or password. Please try again.", "danger")
            return render_template("login.html", form=form, title="Sign In")

        if not user.is_active:
            flash("Your account is disabled. Contact an administrator.", "warning")
            return render_template("login.html", form=form, title="Sign In")

        login_user(user, remember=form.remember_me.data)

        # If the admin flagged a password change on first login
        if user.must_change_password:
            flash("Please change your password before continuing.", "warning")
            return redirect(url_for("auth.change_password"))

        next_page = request.args.get("next")
        if _is_safe_redirect(next_page):
            return redirect(next_page)
        return redirect(url_for("dashboard.overview"))

    return render_template("login.html", form=form, title="Sign In")


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    """Log out the current user through a CSRF-protected form submission."""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """Allow users to change their own password."""
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("Current password is incorrect.", "danger")
            return render_template(
                "change_password.html", form=form, title="Change Password"
            )

        current_user.set_password(form.new_password.data)
        current_user.must_change_password = False
        db.session.commit()
        flash("Password changed successfully!", "success")
        return redirect(url_for("dashboard.overview"))

    return render_template("change_password.html", form=form, title="Change Password")
