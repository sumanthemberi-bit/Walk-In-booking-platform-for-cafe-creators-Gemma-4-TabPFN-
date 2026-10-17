"""Daily backups of the platform database and its encryption key.

Both files are needed to restore: platform.db (all data) and .secret_key (decrypts stored
email passwords and keeps everyone logged in).

Runs automatically once a day while the server is up, and can be run by hand:
    python backup.py
Set WALKIN_BACKUP_DIR to a synced folder (e.g. your Google Drive folder) to keep copies off the laptop.
"""
import datetime as dt
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import db
import vault

BACKUP_DIR = Path(os.getenv("WALKIN_BACKUP_DIR", Path(__file__).parent / "backups"))
KEEP = int(os.getenv("WALKIN_BACKUP_KEEP", "14"))


def make_backup() -> Path:
    """Consistent copy even while the app is writing (SQLite online backup)."""
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    folder = BACKUP_DIR / stamp
    folder.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(db.DB_PATH)
    dst = sqlite3.connect(folder / "platform.db")
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    vault.key()  # make sure the key file exists
    shutil.copy2(vault.KEY_FILE, folder / ".secret_key")
    prune()
    return folder


def list_backups() -> list[str]:
    if not BACKUP_DIR.exists():
        return []
    return sorted((p.name for p in BACKUP_DIR.iterdir() if p.is_dir() and (p / "platform.db").exists()), reverse=True)


def prune():
    for name in list_backups()[KEEP:]:
        shutil.rmtree(BACKUP_DIR / name, ignore_errors=True)


def backed_up_today() -> bool:
    today = dt.date.today().isoformat()
    return any(n.startswith(today) for n in list_backups())


def _loop():
    time.sleep(60)  # let the server finish starting
    while True:
        try:
            if not backed_up_today():
                print(f"[backup] saved {make_backup()}", flush=True)
        except Exception as e:  # never crash the app over a backup
            print(f"[backup] failed: {e}", flush=True)
        time.sleep(3 * 60 * 60)


def start_daily():
    threading.Thread(target=_loop, daemon=True, name="daily-backup").start()


if __name__ == "__main__":
    print(f"Backup saved to {make_backup()}")
