import logging
import html
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from ..db.session import async_session_factory
from ..db.models import GroupChat, User, Match
from ..config import config

logger = logging.getLogger("gullycric.owner")



OWNER_ID = config.owner_ids[0] if config.owner_ids else 0


def _is_owner(user) -> bool:
    return bool(user and (user.id in config.owner_ids))


async def owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_owner(user):
        if update.message:
            await update.message.reply_html("❌ <b>Access Denied:</b> Owner privileges required.")
        return

    async with async_session_factory() as session:
        user_count = (await session.execute(select(func.count(User.user_id)))).scalar() or 0
        match_count = (await session.execute(select(func.count(Match.match_id)))).scalar() or 0
        total_runs = (await session.execute(select(func.sum(User.total_runs)))).scalar() or 0

    text = (
        f"👑 <b>ULTIMATE CRICKET • OWNER CONTROL PANEL</b> 👑\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Owner ID: <code>{user.id} ({user.first_name})</code>\n"
        f"📊 Registered Users: <b>{user_count:,}</b>\n"
        f"🏏 Total Matches Recorded: <b>{match_count:,}</b>\n"
        f"⚡ Total Career Runs: <b>{total_runs:,}</b>\n\n"
        f"🛠️ <b>Quick Admin Commands:</b>\n"
        f"• <code>/broadcast &lt;text&gt;</code> - Broadcast message to users (owner + mods)\n"
        f"• <code>/mod</code> / <code>/unmod</code> - Make or remove a moderator (owner only)\n"
        f"• <code>/bannigga</code> / <code>/unbannigga</code> - Ban or unban a player (owner + mods)\n"
        f"• <code>/modlist</code> - Show owner and moderators\n"
        f"• <code>/backupdb</code> - Send the database backup to your DM (auto every 30 min)\n"
        f"• <code>/restoredb</code> - Reply to a backup .db file to restore all stats (owner only)\n"
        f"• <code>/donate &lt;user_id or @username&gt; &lt;amount&gt;</code> - Add diamonds\n"
        f"• <code>/serverstats</code> - System diagnostics"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Broadcast", callback_data="owner_broadcast_help")],
        [InlineKeyboardButton("🔄 Refresh Stats", callback_data="owner_refresh")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home")]
    ])

    if update.message:
        await update.message.reply_html(text, reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

BROADCAST_HEADER = "📢 <b>ULTIMATE CRICKET ANNOUNCEMENT</b>\n━━━━━━━━━━━━━━━━━━━━━━"
_BROADCAST_PREFIX = re.compile(r"^/broadcast(?:@\w+)?\s*", re.IGNORECASE)


def _strip_broadcast_prefix(text_html: str) -> str:
    return _BROADCAST_PREFIX.sub("", text_html or "", count=1).strip()


def _build_broadcast_payload(message):
    """Work out what to broadcast. Returns (photo_file_id, body_html, copy_source).

    - Photo sent with a /broadcast caption -> photo + that caption.
    - /broadcast as a reply to a text or photo message -> that message's content
      (anything you type after /broadcast is added underneath).
    - /broadcast as a reply to another kind of message (video, file...) -> copied as is.
    - Plain /broadcast text -> text only.
    """
    typed = _strip_broadcast_prefix(message.text_html or message.caption_html or "")
    if message.photo:
        return message.photo[-1].file_id, typed, None
    replied = message.reply_to_message
    if replied:
        if replied.photo:
            body = replied.caption_html or ""
            return replied.photo[-1].file_id, "\n\n".join(x for x in (body, typed) if x), None
        if replied.text:
            body = replied.text_html or ""
            return None, "\n\n".join(x for x in (body, typed) if x), None
        return None, typed, (replied.chat_id, replied.message_id)
    return None, typed, None


async def _send_announcement(bot, chat_id, photo, body):
    full = BROADCAST_HEADER + ("\n" + body if body else "")
    if photo is None:
        await bot.send_message(chat_id=chat_id, text=full, parse_mode="HTML")
    elif len(full) <= 1024:
        await bot.send_photo(chat_id=chat_id, photo=photo, caption=full, parse_mode="HTML")
    else:  # photo captions are limited to 1024 characters
        await bot.send_photo(chat_id=chat_id, photo=photo, caption=BROADCAST_HEADER, parse_mode="HTML")
        await bot.send_message(chat_id=chat_id, text=body, parse_mode="HTML")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..services.moderation import moderation_service
    user = update.effective_user
    message = update.message
    await moderation_service.ensure_loaded()
    if not message or not user or not moderation_service.is_staff(user.id):
        return

    photo, msg, copy_source = _build_broadcast_payload(message)
    if not photo and not msg and not copy_source:
        await update.message.reply_html(
            "⚠️ <b>How to broadcast</b>\n"
            "• <code>/broadcast Your message here</code>\n"
            "• Send a photo with the caption <code>/broadcast your text</code>\n"
            "• Reply to any message or photo with <code>/broadcast</code>"
        )
        return
    async with async_session_factory() as session:
        # Include any groups from active matches or match history
        from ..services.team_match_service import team_match_service
        from ..services.match_service import match_service
        active_group_ids = set()
        for cid in team_match_service.active_sessions.keys():
            if isinstance(cid, int) and cid < 0:
                active_group_ids.add(cid)
        for s in match_service.active_sessions.values():
            if isinstance(s.chat_id, int) and s.chat_id < 0:
                active_group_ids.add(s.chat_id)

        for gid in active_group_ids:
            grp = await session.get(GroupChat, gid)
            if grp is None:
                session.add(GroupChat(chat_id=gid, title="Match Group", is_active=True))
        if active_group_ids:
            await session.commit()

        users = (
            await session.execute(select(User).where(User.started_bot.is_(True)))
        ).scalars().all()
        groups = (
            await session.execute(select(GroupChat).where(GroupChat.is_active.is_(True)))
        ).scalars().all()

    successful_dms = 0
    successful_groups = 0
    failures = []
    dm_ids = {target.user_id for target in users}
    targets = [(target.user_id, "DM") for target in users]
    targets.extend(
        (target.chat_id, "Group")
        for target in groups
        if target.chat_id not in dm_ids
    )

    for chat_id, target_type in targets:
        try:
            if copy_source:
                await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=copy_source[0],
                    message_id=copy_source[1],
                )
            else:
                await _send_announcement(context.bot, chat_id, photo, msg)
            if target_type == "DM":
                successful_dms += 1
            else:
                successful_groups += 1
        except Exception as error:
            failures.append((target_type, chat_id, str(error)))

    failed_dms = sum(target_type == "DM" for target_type, _, _ in failures)
    failed_groups = len(failures) - failed_dms
    await message.reply_html(
        "📢 <b>Broadcast complete</b>\n"
        f"👤 DMs: <b>{successful_dms}/{len(users)}</b> sent · <b>{failed_dms}</b> failed\n"
        f"👥 Groups: <b>{successful_groups}/{len(groups)}</b> sent · <b>{failed_groups}</b> failed\n"
        f"📨 Total delivered: <b>{successful_dms + successful_groups}</b> · "
        f"Failed: <b>{len(failures)}</b>"
    )
    for offset in range(0, len(failures), 20):
        failure_lines = [
            f"• {target_type} <code>{chat_id}</code>: {html.escape(error[:160])}"
            for target_type, chat_id, error in failures[offset:offset + 20]
        ]
        await message.reply_html(
            "❌ <b>Failed targets</b>\n" + "\n".join(failure_lines)
        )


async def track_group_chat_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat:
        return

    # Track DMs: If a user interacts with the bot in private, mark started_bot = True so broadcast reaches them
    if chat.type == "private":
        user = update.effective_user
        if not user or user.is_bot:
            return
        tracked_dm_users = context.application.bot_data.setdefault(
            "tracked_dm_user_ids", set()
        )
        if user.id in tracked_dm_users:
            return
        async with async_session_factory() as session:
            db_user = await session.get(User, user.id)
            if db_user is None:
                db_user = User(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name or "Player",
                    started_bot=True,
                )
                session.add(db_user)
            elif not db_user.started_bot:
                db_user.started_bot = True
            await session.commit()
        tracked_dm_users.add(user.id)
        return

    if chat.type not in ("group", "supergroup"):
        return

    membership_update = update.my_chat_member
    tracked_chat_ids = context.application.bot_data.setdefault(
        "tracked_group_chat_ids", set()
    )
    if not membership_update and chat.id in tracked_chat_ids:
        return
    if membership_update:
        status = membership_update.new_chat_member.status
        is_active = status not in ("left", "kicked")
    else:
        is_active = True

    async with async_session_factory() as session:
        group = await session.get(GroupChat, chat.id)
        if group is None:
            group = GroupChat(chat_id=chat.id, title=chat.title or "Group")
            session.add(group)
        group.title = chat.title or "Group"
        group.is_active = is_active
        await session.commit()
    if is_active:
        tracked_chat_ids.add(chat.id)
    else:
        tracked_chat_ids.discard(chat.id)

async def addrubies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _donate_command(update, context)


async def donate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _donate_command(update, context)


async def _donate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_owner(user):
        return

    message = update.message
    if not message:
        return

    args = context.args or []
    reply_user = (
        message.reply_to_message.from_user
        if message.reply_to_message and message.reply_to_message.from_user
        else None
    )
    target_id = None
    target_username = None
    target_name = "Player"
    username_not_found = False

    mention_entity = next(
        (entity for entity in (message.entities or []) if entity.type == "text_mention" and entity.user),
        None,
    )
    amount_text = args[-1] if args else None
    if reply_user:
        target_id = reply_user.id
        target_username = reply_user.username
        target_name = reply_user.first_name
    elif mention_entity:
        mentioned_user = mention_entity.user
        target_id = mentioned_user.id
        target_username = mentioned_user.username
        target_name = mentioned_user.first_name
    elif len(args) >= 2 and args[0].isdigit():
        target_id = int(args[0])
    elif len(args) >= 2 and args[0].startswith("@"):
        target_username = args[0][1:]
        async with async_session_factory() as session:
            target = (
                await session.execute(
                    select(User).where(func.lower(User.username) == target_username.lower())
                )
            ).scalar_one_or_none()
            if target:
                target_id = target.user_id
                target_name = target.first_name or "Player"
                target_username = target.username
            else:
                username_not_found = True

    if not target_id or not amount_text:
        if username_not_found:
            await message.reply_html("❌ That @username is not registered. Ask them to start the bot first.")
            return
        await message.reply_html(
            "⚠️ Use <code>/donate &lt;user_id or @username&gt; &lt;amount&gt;</code>, "
            "or reply to a user's message with <code>/donate &lt;amount&gt;</code>."
        )
        return

    try:
        amount = int(amount_text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.reply_html("❌ Amount must be a positive whole number of diamonds.")
        return

    async with async_session_factory() as session:
        db_u = await session.get(User, target_id)
        if not db_u:
            db_u = User(
                user_id=target_id,
                username=target_username,
                first_name=target_name,
                diamonds=100,
            )
            session.add(db_u)
        db_u.diamonds = (db_u.diamonds or 0) + amount
        await session.commit()

    display_name = html.escape(target_name or f"User {target_id}")
    await message.reply_html(
        f"✅ Added <b>{amount:,}</b> 💎 to <b>{display_name}</b>."
    )
