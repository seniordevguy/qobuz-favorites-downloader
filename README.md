# Qobuz Downloader
This is a Python app that runs continuously to download favorites from your Qobuz account. Optimized for low-power NAS systems with a built-in web UI for monitoring.

Docker: rgallione/qobuz-downloader:latest
Docker Hub Link: https://hub.docker.com/r/rgallione/qobuz-downloader

## Features
- Automatic downloading of favorited tracks, albums, and artists
- Web UI dashboard for monitoring status and statistics
- Optimized for low-power NAS systems (4-core CPUs with arr stack)
- Multi-stage Docker build for minimal image size
- Configurable resource usage and check intervals
- Health checks for container monitoring

## Configuration

### Directory Linking
The app uses two main directories:
- `/downloads`: The location of music downloaded
- `/config`: The location of the database

You will need to mount a host directory to these locations in Docker.

### Environment Variables

#### Required Variables
```
QOBUZ_EMAIL=your-email@example.com
QOBUZ_PASSWORD=your-password
```

- `QOBUZ_EMAIL`: Your Qobuz email address
- `QOBUZ_PASSWORD`: Your Qobuz password

#### Optional Variables
```
QUALITY=27
CHECK_INTERVAL_MINUTES=30
MAX_WORKERS_TRACKS=1
MAX_WORKERS_ALBUMS=1
MAX_WORKERS_ARTISTS=1
BATCH_SIZE=10
ENABLE_WEB_UI=true
WEB_UI_PORT=5000
```

- `QUALITY`: Audio quality (default: 27 for highest available)
- `CHECK_INTERVAL_MINUTES`: How often to check for new favorites (default: 30)
- `MAX_WORKERS_TRACKS`: Parallel downloads for tracks (default: 1, recommended for low-power NAS)
- `MAX_WORKERS_ALBUMS`: Parallel downloads for albums (default: 1, recommended for low-power NAS)
- `MAX_WORKERS_ARTISTS`: Parallel downloads for artists (default: 1)
- `BATCH_SIZE`: Number of items to process in each batch (default: 10)
- `ENABLE_WEB_UI`: Enable the web dashboard (default: true)
- `WEB_UI_PORT`: Port for the web UI (default: 5000)
- `LOG_LEVEL`: Logging verbosity - DEBUG, INFO, WARNING, ERROR, CRITICAL (default: INFO)
- `LOG_FILE_MAX_MB`: Maximum log file size in MB before rotation, 0 to disable file logging (default: 10)
- `LOG_FILE_BACKUP_COUNT`: Number of rotated log files to keep (default: 3)

## Web UI

The web UI provides a real-time dashboard showing:
- Current download status with visual indicators
- **Manual trigger button** to download immediately (no need to wait for schedule)
- Pending favorites count (tracks, albums, artists)
- Download statistics (successful/failed)
- Last run and next scheduled run times
- Error messages with details
- Auto-refresh every 5 seconds

Access the web UI at `http://your-nas-ip:5000` (or your configured port).

### Manual Downloads
Click the "Download Now" button in the web UI to immediately trigger a download job. The button will be disabled while a job is running.

## Docker Compose Example

```yaml
version: '3.8'

services:
  qobuz-downloader:
    image: rgallione/qobuz-downloader:latest
    container_name: qobuz-downloader
    environment:
      - QOBUZ_EMAIL=your-email@example.com
      - QOBUZ_PASSWORD=your-password
      - QUALITY=27
      - CHECK_INTERVAL_MINUTES=30
      - MAX_WORKERS_TRACKS=1
      - MAX_WORKERS_ALBUMS=1
      - ENABLE_WEB_UI=true
      - WEB_UI_PORT=5000
    volumes:
      - /path/to/music:/downloads
      - /path/to/config:/config
    ports:
      - "5000:5000"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5000/health').read()"]
      interval: 60s
      timeout: 5s
      retries: 3
      start_period: 10s
```

## Performance Tuning for Low-Power NAS

This application is optimized for 4-core CPUs running alongside arr stack applications:

1. **Default settings use 1 worker** to minimize CPU usage
2. **Batch processing** prevents memory spikes
3. **3-second delays** between batches allow CPU to cool down
4. **Multi-stage Docker build** reduces image size and startup time
5. **Configurable check intervals** let you balance freshness vs resource usage

If you have more CPU headroom, you can increase `MAX_WORKERS_*` values to 2-3 for faster downloads.

## Logging

Logs are stored in the `/config` directory as `qobuz-downloader.log` with automatic rotation:
- Log files automatically rotate when they reach the configured size (default: 10MB)
- Old logs are kept according to `LOG_FILE_BACKUP_COUNT` (default: 3 backups)
- This prevents logs from filling up your NAS storage
- Set `LOG_FILE_MAX_MB=0` to disable file logging and only log to console
- Adjust `LOG_LEVEL` for more or less verbose logging

## Getting Started

1. Copy `.env.example` to `.env` and fill in your Qobuz credentials
2. Update directory paths in `docker-compose.yml` to match your system
3. Run `docker-compose up -d`
4. Access the web UI at `http://your-nas-ip:5000`
5. The first download will start immediately, then repeat every 30 minutes (or your configured interval)

## Questions
Reach out to @jeremywade1337 on Telegram if you have any questions

## Terms of Use
This project was intended for educational purposes only. I am not responsible for how you use this project.
