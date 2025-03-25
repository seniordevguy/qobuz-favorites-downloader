import logging
import os
import schedule
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from qobuz_dl.core import QobuzDL
from dotenv import load_dotenv
import qobuz.api as qobuz_api
import qobuz as qobuz_cl

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
load_dotenv()

qobuz_email = os.environ["QOBUZ_EMAIL"]
qobuz_password = os.environ["QOBUZ_PASSWORD"]
music_directory = os.environ.get("MUSIC_DIRECTORY", "/downloads")
config_directory = os.environ.get("CONFIG_DIRECTORY", "/config")
quality = int(os.environ.get("QUALITY", 27))

# Use a threading.Lock for thread synchronization
job_lock = threading.Lock()
job_running = threading.Event()

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

def batch_download(qobuz_dl, user, items, is_album=True, max_workers=3):
    """
    Download items in parallel batches with a limited number of workers
    """
    successful_items = []
    failed_items = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Create a list of tasks with all necessary information
        tasks = [(qobuz_dl, user, item, is_album) for item in items]
        
        # Process items in batches to avoid overwhelming the system
        batch_size = min(10, len(items))
        
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i+batch_size]
            logger.info(f"Processing batch of {len(batch)} items ({i+1}-{min(i+batch_size, len(items))} of {len(items)})")
            
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
                    
            # Small delay between batches to let system resources recover
            if i + batch_size < len(tasks):
                time.sleep(2)
    
    return successful_items, failed_items

def process_favorites():
    try:
        # initialize the Qobuz client
        qobuz.get_tokens()
        qobuz.initialize_client(qobuz_email, qobuz_password, qobuz.app_id, qobuz.secrets)

        # register your APP_ID
        qobuz_api.register_app(qobuz.app_id, qobuz.secrets)
        qobuz_user = qobuz_cl.User(qobuz_email, qobuz_password)

        # retrieve favorites
        logger.info("Fetching favorite items from Qobuz...")
        favorite_tracks = get_user_favorites(qobuz_user, fav_type="tracks")
        favorite_albums = get_user_favorites(qobuz_user, fav_type="albums")
        favorite_artists = get_user_favorites(qobuz_user, fav_type="artists")

        logger.info(f"Found {len(favorite_tracks)} tracks, {len(favorite_albums)} albums, {len(favorite_artists)} artists")

        # download favorites using the optimized batch method
        if favorite_tracks:
            logger.info("Processing tracks...")
            successful_tracks, failed_tracks = batch_download(qobuz, qobuz_user, favorite_tracks, is_album=False, max_workers=2)
            logger.info(f"Tracks: {len(successful_tracks)} successful, {len(failed_tracks)} failed")

        if favorite_albums:
            logger.info("Processing albums...")
            successful_albums, failed_albums = batch_download(qobuz, qobuz_user, favorite_albums, is_album=True, max_workers=2)
            logger.info(f"Albums: {len(successful_albums)} successful, {len(failed_albums)} failed")

        if favorite_artists:
            logger.info("Processing artists...")
            successful_artists, failed_artists = batch_download(qobuz, qobuz_user, favorite_artists, is_album=False, max_workers=1)
            logger.info(f"Artists: {len(successful_artists)} successful, {len(failed_artists)} failed")

    except Exception as e:
        logger.error(f"Process favorites error: {e}", exc_info=True)
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
    logger.info("Starting scheduler. First job will run immediately.")
    
    # Run the job immediately on startup
    threading.Thread(target=job, daemon=True).start()
    
    # Then schedule it to run every 30 minutes
    schedule.every(30).minutes.do(job)
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
    except Exception as e:
        logger.error(f"Scheduler error: {e}", exc_info=True)

if __name__ == "__main__":
    run_scheduler()
