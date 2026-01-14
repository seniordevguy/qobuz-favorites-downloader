import json
import logging
import os
import schedule
import signal
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from typing import Any

from qobuz_dl.core import QobuzDL
from dotenv import load_dotenv
import qobuz.api as qobuz_api
import qobuz as qobuz_cl

load_dotenv()

# Constants
API_PAGINATION_LIMIT = 50
DOWNLOAD_TIMEOUT_SECONDS = 600
INTER_BATCH_DELAY_SECONDS = 3
DEFAULT_RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 2

# Logging configuration
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
log_file_max_mb = int(os.environ.get("LOG_FILE_MAX_MB", 10))
log_file_backup_count = int(os.environ.get("LOG_FILE_BACKUP_COUNT", 3))
config_directory = os.environ.get("CONFIG_DIRECTORY", "/config")

# Configure root logger
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, log_level, logging.INFO))

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(getattr(logging, log_level, logging.INFO))
console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# File handler with rotation (if enabled)
if log_file_max_mb > 0:
    log_file_path = os.path.join(config_directory, "qobuz-downloader.log")
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=log_file_max_mb * 1024 * 1024,  # Convert MB to bytes
        backupCount=log_file_backup_count
    )
    file_handler.setLevel(getattr(logging, log_level, logging.INFO))
    file_handler.setFormatter(console_formatter)
    logger.addHandler(file_handler)
    logger.info(f"Logging to file: {log_file_path} (max {log_file_max_mb}MB, {log_file_backup_count} backups)")
else:
    logger.info("File logging disabled")

def validate_config() -> None:
    """Validate required environment variables are set."""
    required = ["QOBUZ_EMAIL", "QOBUZ_PASSWORD"]
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

validate_config()

qobuz_email = os.environ["QOBUZ_EMAIL"]
qobuz_password = os.environ["QOBUZ_PASSWORD"]
music_directory = os.environ.get("MUSIC_DIRECTORY", "/downloads")
# config_directory already loaded above for logging
quality = int(os.environ.get("QUALITY", 27))

# CPU-friendly settings for low-power NAS (defaults optimized for 4-core with arr stack)
max_workers_tracks = int(os.environ.get("MAX_WORKERS_TRACKS", 1))
max_workers_albums = int(os.environ.get("MAX_WORKERS_ALBUMS", 1))
max_workers_artists = int(os.environ.get("MAX_WORKERS_ARTISTS", 1))
batch_size = int(os.environ.get("BATCH_SIZE", 10))  # Reasonable batch size
check_interval_minutes = int(os.environ.get("CHECK_INTERVAL_MINUTES", 30))
enable_web_ui = os.environ.get("ENABLE_WEB_UI", "true").lower() == "true"
web_ui_port = int(os.environ.get("WEB_UI_PORT", 5000))

# Web UI Authentication (optional - if not set, no auth required)
web_ui_username = os.environ.get("WEB_UI_USERNAME")
web_ui_password = os.environ.get("WEB_UI_PASSWORD")
web_ui_secret_key = os.environ.get("WEB_UI_SECRET_KEY")

# Warn if auth is not configured when web UI is enabled
if enable_web_ui and not (web_ui_username and web_ui_password):
    logger.warning("Web UI authentication is NOT configured. Set WEB_UI_USERNAME and WEB_UI_PASSWORD for security.")

# Use a threading.Lock for thread synchronization
job_lock = threading.Lock()
job_running = threading.Event()
state_lock = threading.Lock()

# Stats persistence file
STATS_FILE = os.path.join(config_directory, "stats.json")

def get_default_stats() -> dict[str, Any]:
    """Return default stats structure."""
    return {
        "tracks_downloaded": 0,
        "albums_downloaded": 0,
        "artists_downloaded": 0,
        "tracks_failed": 0,
        "albums_failed": 0,
        "artists_failed": 0,
        "last_error": None
    }

def load_stats() -> dict[str, Any]:
    """Load stats from disk if available."""
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load stats from disk: {e}")
    return get_default_stats()

def save_stats(stats: dict[str, Any]) -> None:
    """Persist stats to disk."""
    try:
        with open(STATS_FILE, 'w') as f:
            json.dump(stats, f, indent=2)
    except IOError as e:
        logger.warning(f"Failed to save stats to disk: {e}")

# Shared state for web UI
app_state: dict[str, Any] = {
    "last_run": None,
    "next_run": None,
    "current_status": "idle",
    "stats": load_stats(),
    "current_item": None,
    "favorites_count": {"tracks": 0, "albums": 0, "artists": 0}
}

def update_state(key: str, value: Any) -> None:
    """Thread-safe state update."""
    with state_lock:
        app_state[key] = value

def update_stats(key: str, increment: int = 0, value: Any = None) -> None:
    """Thread-safe stats update with optional persistence."""
    with state_lock:
        if value is not None:
            app_state["stats"][key] = value
        else:
            app_state["stats"][key] += increment
        save_stats(app_state["stats"])

def update_favorites_count(tracks: int, albums: int, artists: int) -> None:
    """Thread-safe favorites count update."""
    with state_lock:
        app_state["favorites_count"] = {
            "tracks": tracks,
            "albums": albums,
            "artists": artists
        }

qobuz = QobuzDL(
    directory=music_directory,
    quality=quality,
    downloads_db=os.path.join(config_directory, "db"),
    folder_format="{artist}/{artist} - {album}",
)

def get_user_favorites(user: qobuz_cl.User, fav_type: str) -> list[Any]:
    """
    Returns all user favorites using pagination.

    Args:
        user: Qobuz user instance
        fav_type: Favorites type - 'tracks', 'albums', or 'artists'

    Returns:
        List of favorite items
    """
    offset = 0
    favorites: list[Any] = []

    try:
        while True:
            favs = user.favorites_get(fav_type=fav_type, limit=API_PAGINATION_LIMIT, offset=offset)
            if not favs:
                break
            favorites.extend(favs)
            offset += API_PAGINATION_LIMIT
            logger.debug(f"Retrieved {len(favs)} {fav_type} favorites (total: {len(favorites)})")
    except Exception as e:
        logger.error(f"Error retrieving {fav_type} favorites: {e}")

    return favorites

def download_item(args: tuple[QobuzDL, qobuz_cl.User, Any, bool]) -> tuple[bool, Any]:
    """Worker function to download a single item."""
    qobuz_dl, user, item, is_album = args
    try:
        qobuz_dl.download_from_id(item.id, is_album)
        user.favorites_del(item)
        return (True, item)
    except Exception as e:
        logger.error(f"Failed to download item ID {item.id}: {e}")
        return (False, item)


def download_item_with_retry(
    args: tuple[QobuzDL, qobuz_cl.User, Any, bool],
    max_retries: int = DEFAULT_RETRY_ATTEMPTS
) -> tuple[bool, Any]:
    """
    Worker function to download a single item with retry logic.

    Args:
        args: Tuple of (qobuz_dl, user, item, is_album)
        max_retries: Maximum number of retry attempts

    Returns:
        Tuple of (success, item)
    """
    qobuz_dl, user, item, is_album = args

    for attempt in range(max_retries):
        try:
            qobuz_dl.download_from_id(item.id, is_album)
            user.favorites_del(item)
            return (True, item)
        except Exception as e:
            if attempt < max_retries - 1:
                delay = RETRY_BASE_DELAY_SECONDS ** (attempt + 1)
                logger.warning(
                    f"Download failed for item ID {item.id} (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)
            else:
                logger.error(f"Failed to download item ID {item.id} after {max_retries} attempts: {e}")

    return (False, item)

def batch_download(
    qobuz_dl: QobuzDL,
    user: qobuz_cl.User,
    items: list[Any],
    is_album: bool = True,
    max_workers: int = 1,
    current_batch_size: int = 10
) -> tuple[list[Any], list[Any]]:
    """
    Download items in parallel batches with a limited number of workers.
    Optimized for low-power NAS systems.

    Args:
        qobuz_dl: QobuzDL instance for downloading
        user: Qobuz user instance
        items: List of items to download
        is_album: Whether items are albums
        max_workers: Number of parallel workers
        current_batch_size: Items per batch

    Returns:
        Tuple of (successful_items, failed_items)
    """
    successful_items: list[Any] = []
    failed_items: list[Any] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Create a list of tasks with all necessary information
        tasks = [(qobuz_dl, user, item, is_album) for item in items]

        # Process items in batches to avoid overwhelming the system
        effective_batch_size = min(current_batch_size, len(items))

        for i in range(0, len(tasks), effective_batch_size):
            batch = tasks[i:i+effective_batch_size]
            logger.info(f"Processing batch of {len(batch)} items ({i+1}-{min(i+effective_batch_size, len(items))} of {len(items)})")

            # Submit all tasks in this batch (using retry-enabled download)
            futures = [executor.submit(download_item_with_retry, task) for task in batch]

            # Process results as they complete
            for future in futures:
                try:
                    success, item = future.result(timeout=DOWNLOAD_TIMEOUT_SECONDS)
                    if success:
                        successful_items.append(item)
                    else:
                        failed_items.append(item)
                except Exception as e:
                    logger.error(f"Worker thread exception: {e}")
                    update_stats("last_error", value=str(e))

            # Delay between batches to let CPU cool down (important for NAS)
            if i + effective_batch_size < len(tasks):
                time.sleep(INTER_BATCH_DELAY_SECONDS)

    return successful_items, failed_items

def process_favorites() -> None:
    """Main function to process and download all user favorites."""
    try:
        update_state("current_status", "initializing")

        # initialize the Qobuz client
        qobuz.get_tokens()
        qobuz.initialize_client(qobuz_email, qobuz_password, qobuz.app_id, qobuz.secrets)

        # register your APP_ID
        qobuz_api.register_app(qobuz.app_id, qobuz.secrets)
        qobuz_user = qobuz_cl.User(qobuz_email, qobuz_password)

        # retrieve favorites
        update_state("current_status", "fetching favorites")
        logger.info("Fetching favorite items from Qobuz...")
        favorite_tracks = get_user_favorites(qobuz_user, fav_type="tracks")
        favorite_albums = get_user_favorites(qobuz_user, fav_type="albums")
        favorite_artists = get_user_favorites(qobuz_user, fav_type="artists")

        update_favorites_count(
            tracks=len(favorite_tracks),
            albums=len(favorite_albums),
            artists=len(favorite_artists)
        )

        logger.info(f"Found {len(favorite_tracks)} tracks, {len(favorite_albums)} albums, {len(favorite_artists)} artists")

        # download favorites using the optimized batch method with configurable workers
        if favorite_tracks:
            update_state("current_status", "downloading tracks")
            logger.info("Processing tracks...")
            successful_tracks, failed_tracks = batch_download(
                qobuz, qobuz_user, favorite_tracks,
                is_album=False,
                max_workers=max_workers_tracks,
                current_batch_size=batch_size
            )
            update_stats("tracks_downloaded", increment=len(successful_tracks))
            update_stats("tracks_failed", increment=len(failed_tracks))
            logger.info(f"Tracks: {len(successful_tracks)} successful, {len(failed_tracks)} failed")

        if favorite_albums:
            update_state("current_status", "downloading albums")
            logger.info("Processing albums...")
            successful_albums, failed_albums = batch_download(
                qobuz, qobuz_user, favorite_albums,
                is_album=True,
                max_workers=max_workers_albums,
                current_batch_size=batch_size
            )
            update_stats("albums_downloaded", increment=len(successful_albums))
            update_stats("albums_failed", increment=len(failed_albums))
            logger.info(f"Albums: {len(successful_albums)} successful, {len(failed_albums)} failed")

        if favorite_artists:
            update_state("current_status", "downloading artists")
            logger.info("Processing artists...")
            successful_artists, failed_artists = batch_download(
                qobuz, qobuz_user, favorite_artists,
                is_album=False,
                max_workers=max_workers_artists,
                current_batch_size=batch_size
            )
            update_stats("artists_downloaded", increment=len(successful_artists))
            update_stats("artists_failed", increment=len(failed_artists))
            logger.info(f"Artists: {len(successful_artists)} successful, {len(failed_artists)} failed")

        update_state("current_status", "idle")
        update_state("last_run", time.time())

    except Exception as e:
        logger.error(f"Process favorites error: {e}", exc_info=True)
        update_stats("last_error", value=str(e))
        update_state("current_status", "error")
    finally:
        # Ensure we always clear the running flag
        job_running.clear()

def job() -> None:
    """Main job function that runs on schedule."""
    if job_running.is_set():
        logger.info("A job is already running. Skipping this execution.")
        return

    # Set the running flag first
    job_running.set()
    logger.info("Job started!")

    try:
        process_favorites()
    except Exception as e:
        logger.error(f"Unhandled exception in job: {e}", exc_info=True)
    finally:
        logger.info("Job finished!")
        # Clear the running flag no matter what
        job_running.clear()


# Graceful shutdown handling
shutdown_event = threading.Event()


def handle_shutdown(signum: int, frame: Any) -> None:
    """Handle shutdown signals gracefully."""
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name} signal, initiating graceful shutdown...")
    shutdown_event.set()

    if job_running.is_set():
        logger.info("Waiting for current job to complete (max 60s)...")
        # Wait up to 60 seconds for the current job to finish
        start_time = time.time()
        while job_running.is_set() and (time.time() - start_time) < 60:
            time.sleep(1)

        if job_running.is_set():
            logger.warning("Job did not complete within timeout, forcing shutdown")
        else:
            logger.info("Job completed, shutting down cleanly")

    logger.info("Shutdown complete")
    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


def run_scheduler() -> None:
    """Run the scheduler in the main thread."""
    logger.info(f"Starting scheduler. First job will run immediately, then every {check_interval_minutes} minutes.")

    # Run the job immediately on startup
    threading.Thread(target=job, daemon=True).start()

    # Then schedule it to run at the configured interval
    schedule.every(check_interval_minutes).minutes.do(job)

    try:
        while not shutdown_event.is_set():
            schedule.run_pending()
            # Update next run time for web UI
            jobs = schedule.get_jobs()
            if jobs:
                next_run = jobs[0].next_run.timestamp() if jobs[0].next_run else None
                update_state("next_run", next_run)
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
    except Exception as e:
        logger.error(f"Scheduler error: {e}", exc_info=True)

if __name__ == "__main__":
    # Start web UI if enabled
    if enable_web_ui:
        auth_status = "enabled" if (web_ui_username and web_ui_password) else "DISABLED (not recommended)"
        logger.info(f"Starting web UI on port {web_ui_port} (authentication: {auth_status})")
        from web_ui import create_app
        web_app = create_app(
            app_state,
            job_running,
            job_function=job,
            auth_username=web_ui_username,
            auth_password=web_ui_password,
            secret_key=web_ui_secret_key
        )

        # Run Flask in a separate thread
        web_thread = threading.Thread(
            target=lambda: web_app.run(host='0.0.0.0', port=web_ui_port, debug=False, use_reloader=False),
            daemon=True
        )
        web_thread.start()

    run_scheduler()
