from flask import Flask, render_template, jsonify, request
import time
from datetime import datetime
import threading

def create_app(app_state, job_running, job_function=None):
    """Create and configure the Flask app"""
    app = Flask(__name__)

    @app.route('/')
    def index():
        """Main dashboard page"""
        return render_template('index.html')

    @app.route('/health')
    def health():
        """Health check endpoint for Docker"""
        return jsonify({"status": "healthy"}), 200

    @app.route('/api/status')
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
            "current_item": app_state["current_item"]
        }
        return jsonify(status)

    @app.route('/api/stats')
    def get_stats():
        """Get download statistics"""
        return jsonify(app_state["stats"])

    @app.route('/api/trigger', methods=['POST'])
    def trigger_job():
        """Manually trigger a download job"""
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

        # Start the job in a separate thread
        threading.Thread(target=job_function, daemon=True).start()

        return jsonify({
            "success": True,
            "message": "Download job triggered successfully"
        }), 200

    def format_timestamp(ts):
        """Convert timestamp to readable format"""
        if ts is None:
            return "Never"
        try:
            return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
        except:
            return "Unknown"

    return app
