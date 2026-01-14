import threading
import time
from datetime import datetime
from typing import Any, Callable

from flask import Flask, render_template, jsonify


# Rate limiting configuration
TRIGGER_COOLDOWN_SECONDS = 5
last_trigger_time: float = 0
trigger_lock = threading.Lock()


def create_app(
    app_state: dict[str, Any],
    job_running: threading.Event,
    job_function: Callable[[], None] | None = None
) -> Flask:
    """
    Create and configure the Flask app.

    Args:
        app_state: Shared application state dictionary
        job_running: Event indicating if a job is running
        job_function: Optional function to trigger downloads

    Returns:
        Configured Flask application
    """
    app = Flask(__name__)

    @app.route('/')
    def index() -> str:
        """Main dashboard page."""
        return render_template('index.html')

    @app.route('/health')
    def health() -> tuple[Any, int]:
        """Health check endpoint for Docker."""
        return jsonify({"status": "healthy"}), 200

    @app.route('/api/status')
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
            "current_item": app_state["current_item"]
        }
        return jsonify(status)

    @app.route('/api/stats')
    def get_stats() -> Any:
        """Get download statistics."""
        return jsonify(app_state["stats"])

    @app.route('/api/trigger', methods=['POST'])
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
