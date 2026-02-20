# Immich Album Symlink Sync

Python script to create a 1-to-1 local filesystem representation of your Immich albums using symbolic links. 

This tool is perfect for users who want to expose their Immich albums to other services (like Jellyfin, Screensavers, or basic file browsers) without duplicating large media files. Using symlinks it is possible to have any single photo show up in multiple album folders.

This symlink album method was inspired by gilesknap/gphotos-sync which was a fantastic tool back when google allowed it to work.

## Features
- **Zero Storage Overhead**: Uses symlinks to point to your existing Immich library.
- **Self-Healing**: Automatically detects and repairs broken links or renamed albums.
- **Orphan Cleanup**: Deletes local files and folders that are no longer in your Immich albums.
- **Performant**: Uses asynchronous API calls and local caching to sync thousands of photos in seconds.
- **Portable & Docker-Aware**: Automatically translates internal Docker paths to your host filesystem paths.

## 🧠 How it Works
1. Health Check: Ensures the Immich server is reachable before touching the filesystem.
2. Parallel Fetch: Gathers all album and asset data simultaneously using asyncio.
3. Dynamic Mapping: Compares "Immich's" internal paths to your IMMICH_LIBRARY_PATH to translate them for the host system.
4. Symlink Sync: Creates a Year/Month folder structure and generates symlinks for each asset.
5. Orphan Cleanup: Scans the target directory and removes any files or empty folders no longer present in Immich.

## 🖥️ Deployment Note
For the symbolic links to function correctly, **run this script directly on the machine** hosting your Immich Docker container. Running it inside a container often prevents the symlinks from resolving to the physical storage paths on the host.

## 🛠 Configuration
Open the script and edit the **CONFIGURATION** section at the top:

- 'IMMICH_URL': The URL of your Immich server. Use http://localhost:2283 if running on the same machine as the Immich Docker container.
- `API_KEY`: Your Immich API Key (Settings > API Keys). Permission needed = asset.read, album.read, server.about
- `TARGET_ROOT`: The local path where you want the synced albums to be created.
- `IMMICH_LIBRARY_PATH`: The local path on your **host machine** where your Immich photos are stored.
- `EMAIL_ENABLED`: Set to `True` or `False` to toggle error notifications.
- `SMTP Settings`: Configure your email server if notifications are enabled.

## 📋 Prerequisites
The script requires Python 3.8+ and the `aiohttp` library. On Debian/Ubuntu systems, it is recommended to install this via the system package manager:

```bash
sudo apt update
sudo apt install python3-aiohttp
```
## ⏱ Automation (Cron Job)
To keep your library in sync automatically, set up a Cron job. The example below runs the sync every 6 hours and logs the output for troubleshooting.
Open your crontab: crontab -e
Add the following line (adjust paths for your setup):
```bash
# Runs every 6 hours at the start of the hour
0 */6 * * * /usr/bin/python3 /home/user/scripts/immich_sync.py >> /home/user/scripts/immich_sync_cron.log 2>&1
```
## Other Notes
- This has not been tested with an External Library as I do not have one to test it on. It may work, use at your own risk
- The script is hard coded to create albums with the following folder structure: /Album/YYYY/YYYY-MM/photo.jpg
- This code was generated with AI assistance
