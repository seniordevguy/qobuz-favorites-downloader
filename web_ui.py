import threading
from urllib.parse import urljoin, urlparse

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user

from auth import (
    csrf_protect,
    generate_csrf_token,
    limiter,
    login_required,
    setup_auth,
    validate_csrf_token,
)
from models import User


def create_app(app_state, job_running, job_function=None):
    """Create and configure the Flask app"""
    app = Flask(__name__)
    setup_auth(app)

    @app.before_request
    def refresh_authenticated_session():
        if app.config.get("AUTH_ENABLED", False) and current_user.is_authenticated:
            session.permanent = True

    @app.context_processor
    def inject_template_helpers():
        return {
            "csrf_token": generate_csrf_token,
            "auth_enabled": app.config.get("AUTH_ENABLED", False),
            "auth_warning": app.config.get("AUTH_WARNING"),
        }

    @app.errorhandler(429)
    def handle_rate_limit(error):
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "message": "Too many requests"}), 429

        return render_template(
            "login.html",
            error="Too many failed login attempts. Try again in 15 minutes.",
            next_target=request.args.get("next") or url_for("index"),
        ), 429

    @app.route("/login", methods=["GET", "POST"])
    @limiter.limit(
        "5 per 15 minutes",
        methods=["POST"],
        deduct_when=lambda response: response.status_code == 401,
    )
    def login():
        if not app.config.get("AUTH_ENABLED", False):
            return redirect(url_for("index"))

        if current_user.is_authenticated:
            return redirect(url_for("index"))

        next_target = _safe_redirect_target(request.args.get("next") or request.form.get("next"))

        if request.method == "POST":
            if not validate_csrf_token(request.form.get("csrf_token")):
                app.logger.warning("Login CSRF validation failed from %s", request.remote_addr)
                return (
                    render_template(
                        "login.html",
                        error="Session expired. Refresh the page and try again.",
                        next_target=next_target,
                    ),
                    400,
                )

            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            remember_me = request.form.get("remember_me") == "on"

            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                login_user(user, remember=remember_me)
                session.permanent = True
                app.logger.info("Successful web login for user '%s' from %s", username, request.remote_addr)
                return redirect(next_target)

            app.logger.warning("Failed web login attempt for user '%s' from %s", username, request.remote_addr)
            return (
                render_template(
                    "login.html",
                    error="Invalid username or password.",
                    next_target=next_target,
                ),
                401,
            )

        return render_template("login.html", next_target=next_target)

    @app.route("/logout", methods=["POST"])
    @login_required
    @csrf_protect
    def logout():
        if current_user.is_authenticated:
            app.logger.info("Web logout for user '%s'", current_user.username)
            logout_user()

        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def index():
        """Main dashboard page"""
        return render_template("index.html")

    @app.route("/health")
    def health():
        """Health check endpoint for Docker"""
        return jsonify({"status": "healthy"}), 200

    @app.route("/api/status")
    @login_required
    def get_status():
        """Get current application status"""
        status = {
            "is_running": job_running.is_set(),
            "current_status": app_state["current_status"],
            "last_run": format_timestamp(app_state["last_run"]),
            "last_run_timestamp": app_state["last_run"],
            "next_run": format_timestamp(app_state["next_run"]),
            "next_run_timestamp": app_state["next_run"],
            "stats": app_state["stats"],
            "favorites_count": app_state["favorites_count"],
            "current_item": app_state["current_item"],
        }
        return jsonify(status)

    @app.route("/api/stats")
    @login_required
    def get_stats():
        """Get download statistics"""
        return jsonify(app_state["stats"])

    @app.route("/api/trigger", methods=["POST"])
    @login_required
    @csrf_protect
    def trigger_job():
        """Manually trigger a download job"""
        if job_running.is_set():
            return jsonify({"success": False, "message": "A job is already running"}), 409

        if job_function is None:
            return jsonify({"success": False, "message": "Job function not available"}), 500

        # Start the job in a separate thread
        threading.Thread(target=job_function, daemon=True).start()

        return jsonify({"success": True, "message": "Download job triggered successfully"}), 200

    def format_timestamp(ts):
        """Convert timestamp to readable format"""
        if ts is None:
            return "Never"
        try:
            from datetime import datetime

            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "Unknown"

    return app


def _safe_redirect_target(target):
    if not target:
        return url_for("index")

    host_url = request.host_url
    ref_url = urlparse(host_url)
    test_url = urlparse(urljoin(host_url, target))

    if test_url.scheme in {"http", "https"} and ref_url.netloc == test_url.netloc:
        path = test_url.path or "/"
        query = f"?{test_url.query}" if test_url.query else ""
        return f"{path}{query}"

    return url_for("index")
