# Qobuz Downloader
This is a Python app that runs continuously to download favorites from your Qobuz account. Optimized for low-power NAS systems with a built-in web UI for monitoring.

Docker: rgallione/qobuz-downloader:latest
Docker Hub Link: https://hub.docker.com/r/rgallione/qobuz-downloader

## Features
- Automatic downloading of favorited tracks, albums, and artists
- Web UI dashboard for monitoring status and statistics
- **Secure authentication** for remote access
- Optimized for low-power NAS systems (4-core CPUs with arr stack)
- **Multi-architecture support** (amd64, arm64, arm/v7) for various NAS devices
- Multi-stage Docker build for minimal image size
- Configurable resource usage and check intervals
- **Automatic retry** with exponential backoff for failed downloads
- **Graceful shutdown** handling for clean container stops
- **Persistent statistics** that survive restarts
- Health checks for container monitoring

## Configuration

### Directory Linking
The app uses two main directories:
- `/downloads`: The location of music downloaded
- `/config`: The location of the database, logs, and statistics

You will need to mount a host directory to these locations in Docker.

### Environment Variables

#### Required Variables
```
QOBUZ_EMAIL=your-email@example.com
QOBUZ_PASSWORD=your-password
```

- `QOBUZ_EMAIL`: Your Qobuz email address
- `QOBUZ_PASSWORD`: Your Qobuz password

#### Authentication Variables (Recommended for Remote Access)
```
WEB_UI_USERNAME=admin
WEB_UI_PASSWORD=your-secure-password
WEB_UI_SECRET_KEY=your-secret-key  # Optional, auto-generated if not set
```

- `WEB_UI_USERNAME`: Username for web UI login
- `WEB_UI_PASSWORD`: Password for web UI login
- `WEB_UI_SECRET_KEY`: Secret key for session encryption (optional - auto-generated if not set)

> **Security Note:** If you plan to expose the web UI publicly or access it remotely, you should **always** set `WEB_UI_USERNAME` and `WEB_UI_PASSWORD`. Without these, the dashboard is accessible without authentication.

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
LOG_LEVEL=INFO
LOG_FILE_MAX_MB=10
LOG_FILE_BACKUP_COUNT=3
```

- `QUALITY`: Audio quality (default: 27 for highest available)
  - `5`: MP3 320kbps
  - `6`: FLAC 16-bit/44.1kHz
  - `7`: FLAC 24-bit up to 96kHz
  - `27`: FLAC 24-bit up to 192kHz (highest quality)
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

### Authentication

When `WEB_UI_USERNAME` and `WEB_UI_PASSWORD` are configured:
- A login page is displayed before accessing the dashboard
- Sessions expire after 24 hours of inactivity
- Brute-force protection: 5 failed attempts results in a 5-minute lockout
- Rate limiting prevents rapid login attempts
- Logout button available in the dashboard

### Manual Downloads
Click the "Download Now" button in the web UI to immediately trigger a download job. The button will be disabled while a job is running. Rate limiting prevents accidental rapid triggers.

## Docker Compose Example

### Basic Setup (Local Network Only)
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
      - ENABLE_WEB_UI=true
      - WEB_UI_PORT=5000
    volumes:
      - /path/to/music:/downloads
      - /path/to/config:/config
    ports:
      - "5000:5000"
    restart: unless-stopped
```

### Secure Setup (Remote Access)
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
      - ENABLE_WEB_UI=true
      - WEB_UI_PORT=5000
      # Authentication for remote access
      - WEB_UI_USERNAME=admin
      - WEB_UI_PASSWORD=your-secure-password-here
    volumes:
      - /path/to/music:/downloads
      - /path/to/config:/config
    ports:
      - "5000:5000"
    restart: unless-stopped
```

## Reliability Features

### Automatic Retry
Failed downloads are automatically retried up to 3 times with exponential backoff:
- 1st retry: 2 second delay
- 2nd retry: 4 second delay
- 3rd retry: 8 second delay

This handles temporary network issues and API rate limits gracefully.

### Graceful Shutdown
When the container receives a stop signal (SIGTERM/SIGINT):
1. The application waits for any running download job to complete (up to 60 seconds)
2. Statistics are saved to disk
3. Clean shutdown occurs

This prevents interrupted downloads and data corruption.

### Persistent Statistics
Download statistics are saved to `/config/stats.json` and persist across container restarts. You won't lose your download counts when updating the container.

## Performance Tuning for Low-Power NAS

This application is optimized for 4-core CPUs running alongside arr stack applications:

1. **Default settings use 1 worker** to minimize CPU usage
2. **Batch processing** prevents memory spikes
3. **3-second delays** between batches allow CPU to cool down
4. **Multi-stage Docker build** reduces image size and startup time
5. **Configurable check intervals** let you balance freshness vs resource usage

If you have more CPU headroom, you can increase `MAX_WORKERS_*` values to 2-3 for faster downloads.

## Multi-Architecture Support

The Docker image supports multiple architectures:
- `linux/amd64` - Standard x86_64 servers and NAS devices
- `linux/arm64` - ARM-based NAS (Synology DS923+, QNAP ARM models, etc.)
- `linux/arm/v7` - Older ARM devices (Raspberry Pi 3/4, etc.)

The correct architecture is automatically selected when pulling the image.

## Logging

Logs are stored in the `/config` directory as `qobuz-downloader.log` with automatic rotation:
- Log files automatically rotate when they reach the configured size (default: 10MB)
- Old logs are kept according to `LOG_FILE_BACKUP_COUNT` (default: 3 backups)
- This prevents logs from filling up your NAS storage
- Set `LOG_FILE_MAX_MB=0` to disable file logging and only log to console
- Adjust `LOG_LEVEL` for more or less verbose logging

## Getting Started

1. Copy `.env.example` to `.env` and fill in your Qobuz credentials
2. **For remote access:** Set `WEB_UI_USERNAME` and `WEB_UI_PASSWORD`
3. Update directory paths in `docker-compose.yml` to match your system
4. Run `docker-compose up -d`
5. Access the web UI at `http://your-nas-ip:5000`
6. The first download will start immediately, then repeat every 30 minutes (or your configured interval)

## Security Recommendations

If exposing the web UI to the internet:
1. **Always enable authentication** with strong credentials
2. Use a reverse proxy (nginx, Traefik, Caddy) with HTTPS
3. Consider using a VPN instead of direct exposure
4. Keep the container updated for security patches

## Questions
Reach out to @jeremywade1337 on Telegram if you have any questions

## Terms of Use
This project was intended for educational purposes only. I am not responsible for how you use this project.
