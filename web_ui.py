import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable

from flask import Flask, render_template, jsonify, request, session, redirect, url_for


# Rate limiting configuration
TRIGGER_COOLDOWN_SECONDS = 5
LOGIN_RATE_LIMIT_SECONDS = 2
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300  # 5 minutes

last_trigger_time: float = 0
trigger_lock = threading.Lock()

# Login attempt tracking
login_attempts: dict[str, list[float]] = {}
login_attempts_lock = threading.Lock()


def constant_time_compare(a: str, b: str) -> bool:
    """Compare two strings in constant time to prevent timing attacks."""
    return hmac.compare_digest(a.encode('utf-8'), b.encode('utf-8'))


def check_login_rate_limit(ip: str) -> tuple[bool, str]:
    """
    Check if an IP is rate limited for login attempts.

    Returns:
        Tuple of (is_allowed, error_message)
    """
    with login_attempts_lock:
        now = time.time()

        if ip not in login_attempts:
            login_attempts[ip] = []

        # Clean old attempts (older than lockout period)
        login_attempts[ip] = [t for t in login_attempts[ip] if now - t < LOGIN_LOCKOUT_SECONDS]

        # Check if locked out
        if len(login_attempts[ip]) >= MAX_LOGIN_ATTEMPTS:
            oldest = min(login_attempts[ip])
            remaining = int(LOGIN_LOCKOUT_SECONDS - (now - oldest))
            return False, f"Too many failed attempts. Try again in {remaining}s"

        # Check rate limit (minimum time between attempts)
        if login_attempts[ip] and now - login_attempts[ip][-1] < LOGIN_RATE_LIMIT_SECONDS:
            return False, "Please wait before trying again"

        return True, ""


def record_failed_login(ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    with login_attempts_lock:
        if ip not in login_attempts:
            login_attempts[ip] = []
        login_attempts[ip].append(time.time())


def clear_login_attempts(ip: str) -> None:
    """Clear login attempts after successful login."""
    with login_attempts_lock:
        if ip in login_attempts:
            del login_attempts[ip]


def create_app(
    app_state: dict[str, Any],
    job_running: threading.Event,
    job_function: Callable[[], None] | None = None,
    auth_username: str | None = None,
    auth_password: str | None = None,
    secret_key: str | None = None,
    app_settings: dict[str, Any] | None = None,
    clear_history_func: Callable[[], None] | None = None,
    clear_failed_func: Callable[[], None] | None = None,
    clear_stats_func: Callable[[], None] | None = None
) -> Flask:
    """
    Create and configure the Flask app with authentication.

    Args:
        app_state: Shared application state dictionary
        job_running: Event indicating if a job is running
        job_function: Optional function to trigger downloads
        auth_username: Username for web UI authentication
        auth_password: Password for web UI authentication
        secret_key: Secret key for session encryption
        app_settings: Application settings for display
        clear_history_func: Function to clear download history
        clear_failed_func: Function to clear failed items
        clear_stats_func: Function to clear all statistics

    Returns:
        Configured Flask application
    """
    app = Flask(__name__)

    # Configure session
    app.secret_key = secret_key or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_SECURE=False,  # Set to True if using HTTPS
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=24)
    )

    # Determine if auth is enabled
    auth_enabled = bool(auth_username and auth_password)

    def login_required(f: Callable) -> Callable:
        """Decorator to require authentication for a route."""
        @wraps(f)
        def decorated_function(*args: Any, **kwargs: Any) -> Any:
            if not auth_enabled:
                return f(*args, **kwargs)

            if not session.get('authenticated'):
                if request.is_json or request.path.startswith('/api/'):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect(url_for('login'))

            # Check session expiry
            last_activity = session.get('last_activity')
            if last_activity:
                last_activity_time = datetime.fromisoformat(last_activity)
                if datetime.now() - last_activity_time > timedelta(hours=24):
                    session.clear()
                    if request.is_json or request.path.startswith('/api/'):
                        return jsonify({"error": "Session expired"}), 401
                    return redirect(url_for('login'))

            # Update last activity
            session['last_activity'] = datetime.now().isoformat()
            return f(*args, **kwargs)
        return decorated_function

    @app.route('/login', methods=['GET', 'POST'])
    def login() -> Any:
        """Login page and authentication handler."""
        if not auth_enabled:
            return redirect(url_for('index'))

        if session.get('authenticated'):
            return redirect(url_for('index'))

        error = None

        if request.method == 'POST':
            ip = request.remote_addr or 'unknown'

            # Check rate limit
            allowed, rate_error = check_login_rate_limit(ip)
            if not allowed:
                error = rate_error
            else:
                username = request.form.get('username', '')
                password = request.form.get('password', '')

                # Constant-time comparison to prevent timing attacks
                username_valid = constant_time_compare(username, auth_username or '')
                password_valid = constant_time_compare(password, auth_password or '')

                if username_valid and password_valid:
                    clear_login_attempts(ip)
                    session.permanent = True
                    session['authenticated'] = True
                    session['last_activity'] = datetime.now().isoformat()
                    session['username'] = username
                    return redirect(url_for('index'))
                else:
                    record_failed_login(ip)
                    error = "Invalid username or password"

        return render_template('login.html', error=error)

    @app.route('/logout')
    def logout() -> Any:
        """Log out and clear session."""
        session.clear()
        return redirect(url_for('login'))

    @app.route('/')
    @login_required
    def index() -> str:
        """Main dashboard page."""
        return render_template('index.html')

    @app.route('/health')
    def health() -> tuple[Any, int]:
        """Health check endpoint for Docker (no auth required)."""
        return jsonify({"status": "healthy"}), 200

    @app.route('/api/status')
    @login_required
    def get_status() -> Any:
        """Get current application status."""
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
            "activity": app_state.get("activity", {})
        }
        return jsonify(status)

    @app.route('/api/stats')
    @login_required
    def get_stats() -> Any:
        """Get download statistics."""
        return jsonify(app_state["stats"])

    @app.route('/api/history')
    @login_required
    def get_history() -> Any:
        """Get download history."""
        history = app_state.get("history", [])
        # Format timestamps for display
        formatted = []
        for item in history:
            formatted.append({
                **item,
                "timestamp_formatted": format_timestamp(item.get("timestamp"))
            })
        return jsonify(formatted)

    @app.route('/api/failed')
    @login_required
    def get_failed() -> Any:
        """Get failed downloads list."""
        failed = app_state.get("failed_items", [])
        # Format timestamps for display
        formatted = []
        for item in failed:
            formatted.append({
                **item,
                "timestamp_formatted": format_timestamp(item.get("timestamp"))
            })
        return jsonify(formatted)

    @app.route('/api/activity')
    @login_required
    def get_activity() -> Any:
        """Get current download activity."""
        return jsonify(app_state.get("activity", {}))

    @app.route('/api/settings')
    @login_required
    def get_settings() -> Any:
        """Get application settings (read-only)."""
        if app_settings:
            return jsonify(app_settings)
        return jsonify({})

    @app.route('/api/clear/history', methods=['POST'])
    @login_required
    def clear_history() -> tuple[Any, int]:
        """Clear download history."""
        if clear_history_func:
            clear_history_func()
            return jsonify({"success": True, "message": "History cleared"}), 200
        return jsonify({"success": False, "message": "Operation not available"}), 500

    @app.route('/api/clear/failed', methods=['POST'])
    @login_required
    def clear_failed() -> tuple[Any, int]:
        """Clear failed items list."""
        if clear_failed_func:
            clear_failed_func()
            return jsonify({"success": True, "message": "Failed items cleared"}), 200
        return jsonify({"success": False, "message": "Operation not available"}), 500

    @app.route('/api/clear/stats', methods=['POST'])
    @login_required
    def clear_stats() -> tuple[Any, int]:
        """Clear all statistics."""
        if clear_stats_func:
            clear_stats_func()
            return jsonify({"success": True, "message": "Statistics cleared"}), 200
        return jsonify({"success": False, "message": "Operation not available"}), 500

    @app.route('/api/trigger', methods=['POST'])
    @login_required
    def trigger_job() -> tuple[Any, int]:
        """Manually trigger a download job with rate limiting."""
        global last_trigger_time

        # Rate limiting check
        with trigger_lock:
            current_time = time.time()
            if current_time - last_trigger_time < TRIGGER_COOLDOWN_SECONDS:
                remaining = TRIGGER_COOLDOWN_SECONDS - (current_time - last_trigger_time)
                return jsonify({
                    "success": False,
                    "message": f"Please wait {remaining:.1f}s before triggering again"
                }), 429

            if job_running.is_set():
                return jsonify({
                    "success": False,
                    "message": "A job is already running"
                }), 409

            if job_function is None:
                return jsonify({
                    "success": False,
                    "message": "Job function not available"
                }), 500

            # Update last trigger time
            last_trigger_time = current_time

        # Start the job in a separate thread
        threading.Thread(target=job_function, daemon=True).start()

        return jsonify({
            "success": True,
            "message": "Download job triggered successfully"
        }), 200

    def format_timestamp(ts: float | None) -> str:
        """Convert timestamp to readable format."""
        if ts is None:
            return "Never"
        try:
            return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
        except (TypeError, ValueError, OSError):
            return "Unknown"

    return app
