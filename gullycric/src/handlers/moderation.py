import html
import logging
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import func, select
from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from ..config import config
from ..db.models import User as UserRecord
from ..db.session import async_session_factory
from ..services.moderation import moderation_service

logger = logging.getLogger("gullycric.moderation")


def _link(user_id: int, name: Optional[str]) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name or str(user_id))}</a>'


async def _resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Target from a replied message, a text mention, @username or numeric user id."""
    message = update.effective_message
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return SimpleNamespace(id=u.id, name=u.first_name or "Player", username=u.username)
    mention = next(
        (e for e in (message.entities or []) if e.type == "text_mention" and e.user), None
    )
    if mention:
        u = mention.user
        return SimpleNamespace(id=u.id, name=u.first_name or "Player", username=u.username)
    if not context.args:
        return None
    raw = context.args[0].strip().lstrip("@")
    if raw.isdigit():
        uid = int(raw)
        async with async_session_factory() as db:
            row = await db.get(UserRecord, uid)
        if row:
            return SimpleNamespace(id=uid, name=row.first_name or "Player", username=row.username)
        return SimpleNamespace(id=uid, name=f"User {uid}", username=None)
    async with async_session_factory() as db:
        row = (await db.execute(
            select(UserRecord).where(func.lower(UserRecord.username) == raw.lower())
        )).scalar_one_or_none()
    if row:
        return SimpleNamespace(id=row.user_id, name=row.first_name or "Player", username=row.username)
    return False  # username given but unknown


async def _need_target(update, target):
    message = update.effective_message
    if target is False:
        await message.reply_html(
            "⚠️ That @username is not registered. Reply to the player's message or use their numeric user ID."
        )
    else:
        await message.reply_html("⚠️ Reply to a player's message, or give their @username / user ID.")


async def mod_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /mod makes someone a moderator."""
    user = update.effective_user
    message = update.effective_message
    await moderation_service.ensure_loaded()
    if not moderation_service.is_owner(user.id):
        await message.reply_html("⚠️ Only the bot owner can make moderators.")
        return
    target = await _resolve_target(update, context)
    if not target:
        await _need_target(update, target)
        return
    if moderation_service.is_owner(target.id):
        await message.reply_html("ℹ️ The owner already has full access.")
        return
    if moderation_service.is_mod(target.id):
        await message.reply_html(f"ℹ️ {_link(target.id, target.name)} is already a moderator.")
        return
    await moderation_service.unban(target.id)
    await moderation_service.add_mod(target.id, target.name, target.username, user.id)
    await message.reply_html(
        f"🛡️ {_link(target.id, target.name)} is now a <b>moderator</b>. They can use "
        "<code>/bannigga</code>, <code>/unbannigga</code> and <code>/broadcast</code>."
    )


async def unmod_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /unmod removes a moderator."""
    user = update.effective_user
    message = update.effective_message
    await moderation_service.ensure_loaded()
    if not moderation_service.is_owner(user.id):
        await message.reply_html("⚠️ Only the bot owner can remove moderators.")
        return
    target = await _resolve_target(update, context)
    if not target:
        await _need_target(update, target)
        return
    if await moderation_service.remove_mod(target.id):
        await message.reply_html(f"✅ {_link(target.id, target.name)} is no longer a moderator.")
    else:
        await message.reply_html("ℹ️ That player is not a moderator.")


async def modlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Anyone: show the owner(s) and all moderators."""
    await moderation_service.ensure_loaded()
    names = {}
    if config.owner_ids:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(UserRecord).where(UserRecord.user_id.in_(list(config.owner_ids)))
            )).scalars().all()
        names = {r.user_id: r.first_name for r in rows if r.first_name}
    cache = context.application.bot_data.setdefault("display_names", {})

    async def display_name(uid: int, known: Optional[str] = None) -> Optional[str]:
        """Real name from the database, else Telegram itself (cached)."""
        if known and not known.startswith("User "):
            return known
        if uid in cache:
            return cache[uid]
        try:
            chat = await context.bot.get_chat(uid)
            name = chat.full_name or chat.first_name or chat.title
        except Exception:
            name = None
        if name:
            cache[uid] = name
        return name

    lines = ["👑 <b>OWNER</b>"]
    for oid in config.owner_ids:
        owner_name = await display_name(oid, names.get(oid)) or "Owner"
        lines.append(f"• {_link(oid, owner_name)}")
    lines.append("")
    lines.append(f"🛡️ <b>MODERATORS ({len(moderation_service.mods)})</b>")
    if moderation_service.mods:
        for uid, info in moderation_service.mods.items():
            handle = f" (@{html.escape(info['username'])})" if info.get("username") else ""
            mod_name = await display_name(uid, info.get("name")) or "Moderator"
            lines.append(f"• {_link(uid, mod_name)}{handle}")
    else:
        lines.append("• No moderators yet.")
    await update.effective_message.reply_html("\n".join(lines))


async def bannigga_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner or moderator: ban a player from using the bot."""
    user = update.effective_user
    message = update.effective_message
    await moderation_service.ensure_loaded()
    if not moderation_service.is_staff(user.id):
        await message.reply_html("⚠️ Only the owner or a moderator can use /bannigga.")
        return
    target = await _resolve_target(update, context)
    if not target:
        await _need_target(update, target)
        return
    if moderation_service.is_owner(target.id):
        await message.reply_html("⚠️ The owner cannot be banned.")
        return
    if moderation_service.is_mod(target.id):
        await message.reply_html("⚠️ A moderator cannot be banned. The owner must remove them with /unmod first.")
        return
    if target.id == context.bot.id:
        await message.reply_html("⚠️ I cannot ban myself.")
        return
    if moderation_service.is_banned(target.id):
        await message.reply_html(f"ℹ️ {_link(target.id, target.name)} is already banned.")
        return
    await moderation_service.ban(target.id, target.name, target.username, user.id)
    await message.reply_html(
        f"🚫 {_link(target.id, target.name)} has been <b>banned</b> and can no longer play."
    )


async def unbannigga_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner or moderator: lift a ban."""
    user = update.effective_user
    message = update.effective_message
    await moderation_service.ensure_loaded()
    if not moderation_service.is_staff(user.id):
        await message.reply_html("⚠️ Only the owner or a moderator can use /unbannigga.")
        return
    target = await _resolve_target(update, context)
    if not target:
        await _need_target(update, target)
        return
    if await moderation_service.unban(target.id):
        await message.reply_html(f"✅ {_link(target.id, target.name)} is unbanned and can play again.")
    else:
        await message.reply_html("ℹ️ That player is not banned.")


async def ban_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs before every other handler and stops updates from banned players."""
    user = update.effective_user
    if not user:
        return
    await moderation_service.ensure_loaded()
    if not moderation_service.is_banned(user.id) or moderation_service.is_staff(user.id):
        return
    try:
        if update.callback_query:
            await update.callback_query.answer("🚫 You are banned from Ultimate Cricket.", show_alert=True)
        elif update.effective_message and (
            (update.effective_chat and update.effective_chat.type == "private")
            or (update.effective_message.text or "").startswith("/")
        ):
            await update.effective_message.reply_html("🚫 <b>You are banned from Ultimate Cricket.</b>")
    except Exception:
        pass
    raise ApplicationHandlerStop
