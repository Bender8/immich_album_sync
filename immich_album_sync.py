import asyncio
import aiohttp
import os
import shutil
import json
import sys
import logging
import smtplib
from email.message import EmailMessage
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# --- CONFIGURATION ---
IMMICH_URL = "http://localhost:2283"  # IF RUNNING ON YOUR HOST MACHINE AND USING THE DEFAULT IMMICH PORT NO NEED TO MODIFY THIS
API_KEY = "YOUR-API-KEY"
TARGET_ROOT = Path('/mnt/storage/Media/immich-albums')  # RE-MAP THIS TO YOUR PERFERD LOCATION OF THE ALBUM FOLDERS
IMMICH_LIBRARY_PATH = Path('/mnt/storage/Media/immich-lib/library')  # RE-MAP THIS TO THE LOCATION OF YOUR IMMICH LIBRARY. THIS FOLDER SHOULD CONTAIN YOUR USER ID FOLDERS, EXAMPLE OF A USER ID FOLDER = ac67cc71-1bc2-432b-8f2e-ce8504df4239

# --- SAFETY CONFIGURATION ---
# Set how many API requests can run at the exact same time.
# 5-10 is usually safe for home servers.
MAX_CONCURRENT_REQUESTS = 10

# --- EMAIL CONFIGURATION ---
EMAIL_ENABLED = True  # CHANGE THIS LINE TO "False" TO DESABLE EMAIL NOTIFICATIONS
EMAIL_SENDER = "youremail@gmail.com"
EMAIL_RECEIVER = "youremial@gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "youremail@gmail.com"
SMTP_PASS = "your-application-password"  # IF YOU DON'T KNOW WHAT THIS IS, A QUICK GOOGLE SEARCH WILL SHOW YOU HOW TO CREATE IT

# --- Optional CONFIGURATION ---
LOG_FILE = TARGET_ROOT / "immich_sync.log"  # IF YOU WOULD LIKE TO STORE YOUR LOG FILE SOMEWHERE OTHER THAN THE ALBUM FOLDER YOU CAN CHANGE THIS
CACHE_FILE = TARGET_ROOT / "immich_symlink_cache.json"  # SAME AS ABOVE BUT FOR YOUR CACHE FILE.

# --------------------------DON'T MAKE ANY CHANGES BELOW THIS LINE------------------------------------------------------

# --- LOGGING SETUP ---
logger = logging.getLogger("ImmichSync")
logger.setLevel(logging.INFO)
TARGET_ROOT.mkdir(parents=True, exist_ok=True)
handler = TimedRotatingFileHandler(LOG_FILE, when="D", interval=1, backupCount=5)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler(sys.stdout))

headers = {"x-api-key": API_KEY, "Accept": "application/json"}


# --- NETWORK FUNCTIONS ---
def send_error_email(error_message):
    if not EMAIL_ENABLED:
        return
    msg = EmailMessage()
    msg.set_content(f"Immich Sync encountered an error:\n\n{error_message}")
    msg['Subject'] = "Immich Sync Error"
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        logger.info("Error email sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")


async def check_immich_health(session):
    try:
        async with session.get(f"{IMMICH_URL}/api/server/ping", headers=headers) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return False


async def fetch_album_assets(session, album, semaphore):
    """
    Fetches assets for an album from Immich v3 by utilizing the modern,
    paginated /api/search/metadata endpoint filtered by albumIds.
    """
    album_id = album['id']
    all_assets = []
    page = 1
    url = f"{IMMICH_URL}/api/search/metadata"

    while True:
        # Wrap the album id inside an array as required by Immich v3
        payload = {"albumIds": [album_id], "size": 1000, "page": page}

        async with semaphore:
            try:
                async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
                    if resp.status != 200:
                        logger.error(f"Failed fetching assets for album {album['albumName']} on page {page}: {resp.status}")
                        raise RuntimeError(f"API Error fetching remote album assets: {resp.status}")

                    data = await resp.json()
                    items = data.get("assets", {}).get("items", [])

                    # If we receive an empty list, we've reached the end of the album's assets
                    if not items:
                        break

                    all_assets.extend(items)
                    page += 1

            except Exception as e:
                logger.error(f"Exception while loading assets for album {album['albumName']} on page {page}: {e}")
                # Pass back whatever we managed to grab up to this point or raise an error to protect from accidental deletion
                raise

    return album, all_assets


# --- FILE SYSTEM FUNCTIONS ---
def translate_path_to_host(api_path, library_path_host):
    if not api_path:
        return None
    marker = "library/"
    if marker in api_path:
        relative_part = api_path.split(marker, 1)[1]
        return library_path_host / relative_part
    return None


def validate_and_link(source, dest, album_name):
    is_broken = dest.is_symlink() and not dest.exists()
    if not dest.exists() or is_broken:
        action = "Repairing broken link" if is_broken else "Creating new link"
        logger.info(f"{action}: {dest.name} in album '{album_name}'")
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            dest.symlink_to(source)
            return True
        except Exception as e:
            logger.error(f"Failed to link {dest}: {e}")
    return False


def load_cache():
    if not CACHE_FILE.exists():
        return {"assets": {}, "album_map": {}}
    try:
        with open(CACHE_FILE, 'r') as f:
            loaded = json.load(f)
            if isinstance(loaded, dict) and "assets" in loaded:
                return loaded
            return {"assets": loaded, "album_map": {}}
    except Exception:
        logger.warning("Cache file corrupted, starting fresh.")
        return {"assets": {}, "album_map": {}}


def process_album_folder(album, album_map):
    album_id = album['id']
    album_name = album['albumName'].replace("/", "-")
    album_dir = TARGET_ROOT / album_name
    old_name = album_map.get(album_id)
    if old_name and old_name != album_name:
        old_dir = TARGET_ROOT / old_name
        if old_dir.exists() and not album_dir.exists():
            logger.info(f"Rename detected: '{old_name}' -> '{album_name}'.")
            try:
                old_dir.rename(album_dir)
            except Exception as e:
                logger.error(f"Failed to rename {old_dir}: {e}")
    return album_id, album_name, album_dir


def sync_asset(asset, album_id, album_name, album_dir, asset_cache, valid_paths):
    api_path = asset.get('originalPath') or asset.get('path')
    source_file = translate_path_to_host(api_path, IMMICH_LIBRARY_PATH)
    if not api_path or not source_file:
        return None, None
    created_at = asset.get('fileCreatedAt', '0000-00-00T')
    dest_file = album_dir / created_at[:4] / f"{created_at[:4]}-{created_at[5:7]}" / os.path.basename(api_path)
    valid_paths.add(str(dest_file))
    for parent in dest_file.parents:
        if parent == TARGET_ROOT:
            break
        valid_paths.add(str(parent))
    asset_key = f"{asset['id']}_{album_id}"
    asset_state = f"{api_path}_{created_at}"
    if asset_cache.get(asset_key) != asset_state or not dest_file.exists():
        validate_and_link(source_file, dest_file, album_name)
    return asset_key, asset_state


def cleanup_orphans(valid_paths):
    logger.info("Scanning for orphans...")
    for root, dirs, files in os.walk(TARGET_ROOT, topdown=False):
        curr = Path(root)
        for name in files:
            p = curr / name
            if str(p) not in valid_paths:
                logger.info(f"Deleting orphan file: {p.relative_to(TARGET_ROOT)}")
                p.unlink(missing_ok=True)
        for name in dirs:
            p = curr / name
            if str(p) not in valid_paths:
                logger.info(f"Deleting removed folder: {p.relative_to(TARGET_ROOT)}")
                if p.exists():
                    shutil.rmtree(p)
            elif p.exists() and not any(p.iterdir()):
                p.rmdir()


# --- MAIN SYNC LOGIC ---
async def sync_immich_albums():
    logger.info("--- Starting Immich Album Sync ---")
    valid_paths = {str(TARGET_ROOT), str(LOG_FILE), str(CACHE_FILE)}
    full_cache = load_cache()
    new_asset_cache, new_album_map = {}, {}

    # Define a semaphore to limit concurrent API calls
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    # 30-second timeout to prevent the script from hanging indefinitely
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        if not await check_immich_health(session):
            raise ConnectionError("Immich API unreachable.")

        # 1. Fetch Albums
        async with session.get(f"{IMMICH_URL}/api/albums?shared=false", headers=headers) as resp:
            albums = await resp.json()
            if isinstance(albums, dict) and "message" in albums:
                raise ValueError(f"API Error: {albums.get('message')}")

        # 2. Fetch Assets for all albums using the semaphore
        logger.info(f"Syncing {len(albums)} albums (Max concurrency: {MAX_CONCURRENT_REQUESTS})...")
        tasks = [fetch_album_assets(session, a, semaphore) for a in albums]
        results = await asyncio.gather(*tasks)

        # 3. Process Results
        for album, assets in results:
            album_id, album_name, album_dir = process_album_folder(album, full_cache["album_map"])
            new_album_map[album_id] = album_name
            for asset in assets:
                key, state = sync_asset(asset, album_id, album_name, album_dir, full_cache["assets"], valid_paths)
                if key:
                    new_asset_cache[key] = state

    # 4. Finalize
    cleanup_orphans(valid_paths)
    with open(CACHE_FILE, 'w') as f:
        json.dump({"assets": new_asset_cache, "album_map": new_album_map}, f)
    logger.info("--- Sync Complete ---")


if __name__ == "__main__":
    try:
        asyncio.run(sync_immich_albums())
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        send_error_email(str(e))
