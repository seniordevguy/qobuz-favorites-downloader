import logging
import os
import schedule
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from qobuz_dl.core import QobuzDL
from dotenv import load_dotenv
import qobuz.api as qobuz_api
import qobuz as qobuz_cl

load_dotenv()

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

# Use a threading.Lock for thread synchronization
job_lock = threading.Lock()
job_running = threading.Event()

# Shared state for web UI
app_state = {
    "last_run": None,
    "next_run": None,
    "current_status": "idle",
    "stats": {
        "tracks_downloaded": 0,
        "albums_downloaded": 0,
        "artists_downloaded": 0,
        "tracks_failed": 0,
        "albums_failed": 0,
        "artists_failed": 0,
        "last_error": None
    },
    "current_item": None,
    "favorites_count": {"tracks": 0, "albums": 0, "artists": 0}
}

qobuz = QobuzDL(
    directory=music_directory,
    quality=quality,
    downloads_db=os.path.join(config_directory, "db"),
    folder_format="{artist}/{artist} - {album}",
)

def get_user_favorites(user: qobuz_cl.User, fav_type):
    '''
    Returns all user favorites using pagination

    Parameters
    ----------
    user: dict
        returned by qobuz.User
    fav_type: str
        favorites type: 'tracks', 'albums', 'artists'
    '''
    limit = 50
    offset = 0
    favorites = []
    
    try:
        while True:
            favs = user.favorites_get(fav_type=fav_type, limit=limit, offset=offset)
            if not favs:
                break
            favorites.extend(favs)
            offset += limit
            logger.debug(f"Retrieved {len(favs)} {fav_type} favorites (total: {len(favorites)})")
    except Exception as e:
        logger.error(f"Error retrieving {fav_type} favorites: {e}")
    
    return favorites

def download_item(args):
    """Worker function to download a single item"""
    qobuz_dl, user, item, is_album = args
    try:
        qobuz_dl.download_from_id(item.id, is_album)
        user.favorites_del(item)
        return (True, item)
    except Exception as e:
        logger.error(f"Failed to download item ID {item.id}: {e}")
        return (False, item)

def batch_download(qobuz_dl, user, items, is_album=True, max_workers=1, current_batch_size=10):
    """
    Download items in parallel batches with a limited number of workers
    Optimized for low-power NAS systems
    """
    successful_items = []
    failed_items = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Create a list of tasks with all necessary information
        tasks = [(qobuz_dl, user, item, is_album) for item in items]

        # Process items in batches to avoid overwhelming the system
        effective_batch_size = min(current_batch_size, len(items))

        for i in range(0, len(tasks), effective_batch_size):
            batch = tasks[i:i+effective_batch_size]
            logger.info(f"Processing batch of {len(batch)} items ({i+1}-{min(i+effective_batch_size, len(items))} of {len(items)})")

            # Submit all tasks in this batch
            futures = [executor.submit(download_item, task) for task in batch]

            # Process results as they complete
            for future in futures:
                try:
                    success, item = future.result(timeout=600)  # 10-minute timeout per item
                    if success:
                        successful_items.append(item)
                    else:
                        failed_items.append(item)
                except Exception as e:
                    logger.error(f"Worker thread exception: {e}")
                    app_state["stats"]["last_error"] = str(e)

            # Delay between batches to let CPU cool down (important for NAS)
            if i + effective_batch_size < len(tasks):
                time.sleep(3)

    return successful_items, failed_items

def process_favorites():
    try:
        app_state["current_status"] = "initializing"

        # initialize the Qobuz client
        qobuz.get_tokens()
        qobuz.initialize_client(qobuz_email, qobuz_password, qobuz.app_id, qobuz.secrets)

        # register your APP_ID
        qobuz_api.register_app(qobuz.app_id, qobuz.secrets)
        qobuz_user = qobuz_cl.User(qobuz_email, qobuz_password)

        # retrieve favorites
        app_state["current_status"] = "fetching favorites"
        logger.info("Fetching favorite items from Qobuz...")
        favorite_tracks = get_user_favorites(qobuz_user, fav_type="tracks")
        favorite_albums = get_user_favorites(qobuz_user, fav_type="albums")
        favorite_artists = get_user_favorites(qobuz_user, fav_type="artists")

        app_state["favorites_count"] = {
            "tracks": len(favorite_tracks),
            "albums": len(favorite_albums),
            "artists": len(favorite_artists)
        }

        logger.info(f"Found {len(favorite_tracks)} tracks, {len(favorite_albums)} albums, {len(favorite_artists)} artists")

        # download favorites using the optimized batch method with configurable workers
        if favorite_tracks:
            app_state["current_status"] = "downloading tracks"
            logger.info("Processing tracks...")
            successful_tracks, failed_tracks = batch_download(
                qobuz, qobuz_user, favorite_tracks,
                is_album=False,
                max_workers=max_workers_tracks,
                current_batch_size=batch_size
            )
            app_state["stats"]["tracks_downloaded"] += len(successful_tracks)
            app_state["stats"]["tracks_failed"] += len(failed_tracks)
            logger.info(f"Tracks: {len(successful_tracks)} successful, {len(failed_tracks)} failed")

        if favorite_albums:
            app_state["current_status"] = "downloading albums"
            logger.info("Processing albums...")
            successful_albums, failed_albums = batch_download(
                qobuz, qobuz_user, favorite_albums,
                is_album=True,
                max_workers=max_workers_albums,
                current_batch_size=batch_size
            )
            app_state["stats"]["albums_downloaded"] += len(successful_albums)
            app_state["stats"]["albums_failed"] += len(failed_albums)
            logger.info(f"Albums: {len(successful_albums)} successful, {len(failed_albums)} failed")

        if favorite_artists:
            app_state["current_status"] = "downloading artists"
            logger.info("Processing artists...")
            successful_artists, failed_artists = batch_download(
                qobuz, qobuz_user, favorite_artists,
                is_album=False,
                max_workers=max_workers_artists,
                current_batch_size=batch_size
            )
            app_state["stats"]["artists_downloaded"] += len(successful_artists)
            app_state["stats"]["artists_failed"] += len(failed_artists)
            logger.info(f"Artists: {len(successful_artists)} successful, {len(failed_artists)} failed")

        app_state["current_status"] = "idle"
        app_state["last_run"] = time.time()

    except Exception as e:
        logger.error(f"Process favorites error: {e}", exc_info=True)
        app_state["stats"]["last_error"] = str(e)
        app_state["current_status"] = "error"
    finally:
        # Ensure we always clear the running flag
        job_running.clear()

def job():
    """Main job function that runs on schedule"""
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

def run_scheduler():
    """Run the scheduler in the main thread"""
    logger.info(f"Starting scheduler. First job will run immediately, then every {check_interval_minutes} minutes.")

    # Run the job immediately on startup
    threading.Thread(target=job, daemon=True).start()

    # Then schedule it to run at the configured interval
    schedule.every(check_interval_minutes).minutes.do(job)

    try:
        while True:
            schedule.run_pending()
            # Update next run time for web UI
            jobs = schedule.get_jobs()
            if jobs:
                app_state["next_run"] = jobs[0].next_run.timestamp() if jobs[0].next_run else None
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
    except Exception as e:
        logger.error(f"Scheduler error: {e}", exc_info=True)

if __name__ == "__main__":
    # Start web UI if enabled
    if enable_web_ui:
        logger.info(f"Starting web UI on port {web_ui_port}")
        from web_ui import create_app
        web_app = create_app(app_state, job_running, job_function=job)

        # Run Flask in a separate thread
        web_thread = threading.Thread(
            target=lambda: web_app.run(host='0.0.0.0', port=web_ui_port, debug=False, use_reloader=False),
            daemon=True
        )
        web_thread.start()

    run_scheduler()
