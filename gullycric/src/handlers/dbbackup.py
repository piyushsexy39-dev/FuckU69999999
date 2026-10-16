import logging
import os
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

from ..config import config
from ..services import db_backup, db_snapshot
from ..services.moderation import moderation_service

logger = logging.getLogger("gullycric.dbbackup")


def _is_owner(user) -> bool:
    return bool(user and user.id in config.owner_ids)


async def backupdb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: send the database to the owner's DM now and pin it."""
    message = update.effective_message
    if not _is_owner(update.effective_user):
        await message.reply_html("⚠️ Only the bot owner can use /backupdb.")
        return
    try:
        status = await db_backup.send_backup(context.bot, reason="manual", force=True)
    except Exception as error:
        logger.warning("Manual backup failed: %s", error)
        await message.reply_html(
            "❌ Backup failed. Open the bot in a private chat and send /start once, then try again.\n"
            f"<code>{type(error).__name__}</code>"
        )
        return
    if status == "sent":
        await message.reply_html("✅ Backup sent to the owner's DM and pinned there.")
    elif status == "too_large":
        await message.reply_html("❌ The database is too big to send through Telegram.")
    else:
        await message.reply_html("ℹ️ Backups are off (this bot is not using the default sqlite database).")


async def restoredb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: reply to a backup .db file with /restoredb to replace all stats with it."""
    message = update.effective_message
    if not _is_owner(update.effective_user):
        await message.reply_html("⚠️ Only the bot owner can use /restoredb.")
        return
    document = message.reply_to_message.document if message.reply_to_message else None
    if not document:
        await message.reply_html(
            "Reply to a database file (the backup or your old <code>gullycric.db</code>) with <code>/restoredb</code>.\n"
            "⚠️ This replaces ALL current stats, coins and leaderboards with the file's data."
        )
        return
    if (document.file_size or 0) > db_backup.MAX_DOWNLOAD_BYTES:
        await message.reply_html("❌ Telegram lets bots download files up to 20 MB only; this file is bigger.")
        return
    try:
        with tempfile.TemporaryDirectory() as workdir:
            local = os.path.join(workdir, "incoming.db")
            telegram_file = await context.bot.get_file(document.file_id)
            await telegram_file.download_to_drive(local)
            ok, reason, users = db_snapshot.validate_backup(local)
            if not ok:
                await message.reply_html(f"❌ Not restored. {reason}")
                return
            restored = await db_backup.restore_database(local)
    except Exception as error:
        logger.error("Restore failed: %s", error, exc_info=True)
        await message.reply_html(f"❌ Restore failed: <code>{type(error).__name__}</code>. Your current data was not changed.")
        return
    # moderators and bans are cached in memory; reload them from the restored database
    moderation_service.mods.clear()
    moderation_service.banned.clear()
    moderation_service.loaded = False
    db_backup._last_digest = None
    await message.reply_html(
        f"✅ Restored <b>{restored}</b> players. Stats, coins and leaderboards now match that file.\n"
        "A copy of the previous data was kept as <code>.before_restore</code> next to the database."
    )
