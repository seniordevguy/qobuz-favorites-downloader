import os
import secrets
from datetime import timedelta
from functools import wraps

from flask import abort, current_app, jsonify, redirect, request, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user

from models import User, db, initialize_database


login_manager = LoginManager()
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def setup_auth(app):
    config_directory = os.environ.get("CONFIG_DIRECTORY", "/config")
    os.makedirs(config_directory, exist_ok=True)
    db_path = os.path.join(config_directory, "users.db")

    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    secret_key = os.environ.get("SESSION_SECRET_KEY")
    if not secret_key:
        secret_key = secrets.token_urlsafe(48)
        app.logger.warning(
            "SESSION_SECRET_KEY is not set; generated ephemeral session key for this startup."
        )

    session_timeout = _parse_positive_int(os.environ.get("SESSION_TIMEOUT_MINUTES", "60"), 60)
    session_cookie_secure = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"

    app.config.update(
        SECRET_KEY=secret_key,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=session_timeout),
        SESSION_REFRESH_EACH_REQUEST=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=session_cookie_secure,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SECURE=session_cookie_secure,
    )

    db.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "login"
    login_manager.login_message_category = "error"
    login_manager.session_protection = "strong"

    limiter.init_app(app)

    default_username = os.environ.get("WEB_UI_USERNAME", "admin")
    default_password = os.environ.get("WEB_UI_PASSWORD")

    with app.app_context():
        auth_enabled = initialize_database(default_username, default_password, logger=app.logger)

    app.config["AUTH_ENABLED"] = auth_enabled
    app.config["AUTH_WARNING"] = None

    if not auth_enabled:
        app.config["AUTH_WARNING"] = (
            "Authentication is disabled because WEB_UI_PASSWORD is not set and no users exist yet."
        )


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_app.config.get("AUTH_ENABLED", False):
            return view_func(*args, **kwargs)

        if current_user.is_authenticated:
            return view_func(*args, **kwargs)

        if request.path.startswith("/api/"):
            return jsonify({"success": False, "message": "Authentication required"}), 401

        next_url = request.url
        return redirect(url_for("login", next=next_url))

    return wrapped


def csrf_protect(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_app.config.get("AUTH_ENABLED", False):
            return view_func(*args, **kwargs)

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
            if not validate_csrf_token(token):
                current_app.logger.warning("CSRF validation failed for path: %s", request.path)
                abort(400, description="Invalid CSRF token")

        return view_func(*args, **kwargs)

    return wrapped


def generate_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def validate_csrf_token(token: str | None) -> bool:
    expected = session.get("csrf_token")
    if not expected or not token:
        return False
    return secrets.compare_digest(expected, token)


def _parse_positive_int(value: str, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default
