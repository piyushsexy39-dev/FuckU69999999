"""Keeps player stats alive across restarts, redeploys and host changes.

The bot sends a snapshot of its database to the owner's Telegram DM and pins it.
When it starts with an empty database (new host, redeploy without a volume) it
downloads the pinned snapshot and restores it automatically.
"""
import asyncio
import datetime
import logging
import os
import re
import shutil
import tempfile
from typing import Optional

from telegram import Bot

from ..config import config
from ..db.session import engine, init_db
from . import db_snapshot

logger = logging.getLogger("gullycric.dbbackup")

BACKUP_FILENAME = "ultimate_cricket_backup.db"
MAX_SEND_BYTES = 45 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Telegram Bot API download limit

restore_failed = False  # set when startup restore could not finish; pauses auto-backup
_last_digest: Optional[str] = None


def db_path() -> Optional[str]:
    return db_snapshot.sqlite_path_from_url(config.database_url)


def backup_chat_id() -> Optional[int]:
    raw = (os.getenv("BACKUP_CHAT_ID") or "").strip()
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return config.owner_ids[0] if config.owner_ids else None


def _is_our_backup(message) -> bool:
    document = getattr(message, "document", None)
    return bool(document and (document.file_name or "").startswith("ultimate_cricket_backup"))


async def restore_database(source_file: str) -> int:
    """Replace the live database with source_file (already validated). Returns player count."""
    target = db_path()
    if not target:
        raise RuntimeError("Backups only work with the default sqlite database.")
    await engine.dispose()
    if os.path.exists(target):
        shutil.copy2(target, target + ".before_restore")
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(target + suffix)
        except OSError:
            pass
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    staging = target + ".incoming"
    shutil.copyfile(source_file, staging)
    os.replace(staging, target)
    await init_db()
    return db_snapshot.count_users(target)


async def restore_latest_if_empty() -> str:
    """Startup: if the database has no players, restore the pinned backup. Returns a status word."""
    global restore_failed
    path = db_path()
    chat_id = backup_chat_id()
    if not path or not chat_id or not config.bot_token:
        return "disabled"
    if await asyncio.to_thread(db_snapshot.count_users, path) > 0:
        return "not_needed"

    for attempt in range(1, 4):
        try:
            with tempfile.TemporaryDirectory() as workdir:
                local = os.path.join(workdir, BACKUP_FILENAME)
                async with Bot(config.bot_token) as bot:
                    pinned = (await bot.get_chat(chat_id)).pinned_message
                    if not pinned or not _is_our_backup(pinned):
                        return "no_backup"
                    if (pinned.document.file_size or 0) > MAX_DOWNLOAD_BYTES:
                        logger.error("Pinned backup is larger than Telegram's 20 MB download limit.")
                        restore_failed = True
                        return "failed"
                    telegram_file = await bot.get_file(pinned.document.file_id)
                    await telegram_file.download_to_drive(local)
                ok, message, users = await asyncio.to_thread(db_snapshot.validate_backup, local)
                if not ok:
                    logger.error("Pinned backup is not usable: %s", message)
                    restore_failed = True
                    return "failed"
                restored = await restore_database(local)
                logger.info("Restored %s players from the pinned Telegram backup.", restored)
                return "restored"
        except Exception as error:
            logger.warning("Restore attempt %s/3 failed: %s", attempt, error)
            await asyncio.sleep(5)
    restore_failed = True
    return "failed"


async def send_backup(bot: Bot, reason: str = "auto", force: bool = False) -> str:
    """Send a snapshot to the backup chat and pin it. Returns a status word."""
    global _last_digest
    path = db_path()
    chat_id = backup_chat_id()
    if not path or not chat_id:
        return "disabled"
    users = await asyncio.to_thread(db_snapshot.count_users, path)
    if users == 0 and not force:
        return "empty"  # never overwrite a good backup with an empty database

    with tempfile.TemporaryDirectory() as workdir:
        local = os.path.join(workdir, BACKUP_FILENAME)
        await asyncio.to_thread(db_snapshot.snapshot, path, local)
        digest = await asyncio.to_thread(db_snapshot.file_digest, local)
        if digest == _last_digest and not force:
            return "unchanged"
        size = os.path.getsize(local)
        if size > MAX_SEND_BYTES:
            logger.error("Database is %s bytes, too large to send to Telegram.", size)
            return "too_large"

        previous = None
        try:
            previous = (await bot.get_chat(chat_id)).pinned_message
        except Exception:
            pass

        stamp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
        caption = (
            "💾 Ultimate Cricket database backup\n"
            f"🕒 {stamp.strftime('%d %b %Y, %H:%M')} IST · {users} players · {size // 1024} KB\n"
            f"({reason}) Pinned automatically. To restore: reply to this file with /restoredb"
        )
        with open(local, "rb") as handle:
            sent = await bot.send_document(
                chat_id=chat_id,
                document=handle,
                filename=BACKUP_FILENAME,
                caption=caption,
                disable_notification=True,
                read_timeout=60,
                write_timeout=60,
            )
        _last_digest = digest
        try:
            await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
            if previous and previous.message_id != sent.message_id and _is_our_backup(previous):
                await bot.unpin_chat_message(chat_id=chat_id, message_id=previous.message_id)
        except Exception as error:
            logger.warning("Backup sent but could not be pinned: %s", error)
    return "sent"


async def run_backup_loop(bot: Bot):
    """Send a backup every BACKUP_INTERVAL_MINUTES (default 30) when something changed."""
    try:
        interval = max(5, int(os.getenv("BACKUP_INTERVAL_MINUTES", "30"))) * 60
    except ValueError:
        interval = 30 * 60
    await asyncio.sleep(120)
    warned = False
    while True:
        try:
            if restore_failed:
                if not warned:
                    logger.error("Auto-backup is paused: the startup restore failed. Restart the bot to retry.")
                    warned = True
            else:
                status = await send_backup(bot, reason="auto")
                logger.info("Auto-backup: %s", status)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Auto-backup failed (has the owner started the bot in DM?): %s", error)
        await asyncio.sleep(interval)
