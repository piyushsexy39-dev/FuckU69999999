from ..config import config
import os
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from ..services.economy import economy_service
from ..services.team_match_service import team_match_service
from ..services.media_service import media_service
from ..services.media_cache import media_cache
from ..db.session import async_session_factory
from ..db.models import User
from .solo import safe_edit_or_reply
from .stats import stats_command, leaderboard_command

logger = logging.getLogger("gullycric.start")

def get_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏏 Play Cricket", callback_data="menu_play"),
        ],
        [
            InlineKeyboardButton("📊 My Stats", callback_data="menu_stats"),
            InlineKeyboardButton("🏆 Leaderboard", callback_data="menu_leaderboard"),
        ],
        [
            InlineKeyboardButton("SexyOwner!!", url=config.owner_contact_url()),
        ]
    ])

def get_help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Solo Mode Guide", callback_data="help_solo"),
            InlineKeyboardButton("👥 Team Mode Guide", callback_data="help_team"),
        ],
        [
            InlineKeyboardButton("👑 Host & Captains", callback_data="help_captains"),
            InlineKeyboardButton("📋 All Commands", callback_data="help_commands"),
        ],
        [
            InlineKeyboardButton("🏏 Start Match", callback_data="menu_play"),
            InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home"),
        ]
    ])

def get_match_desk_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Solo Match", callback_data="select_solo_mode"),
            InlineKeyboardButton("👥 Team Match", callback_data="select_team_mode"),
        ],
        [
            InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home")
        ]
    ])

def render_start_text(user_name: str) -> str:
    return (
        f"🏏 <b>ULTIMATE CRICKET ARENA</b> 🏏\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Welcome, <b>{user_name}</b>!\n\n"
        f"Play authentic street cricket on Telegram with:\n"
        f"• ⚡ <b>Solo Multiplayer:</b> Unlimited overs rotation\n"
        f"• 👥 <b>Team Battles:</b> Team A vs Team B with Captains & Toss\n"
        f"• 🔒 <b>Secret DM Bowling:</b> Pick deliveries privately in DM\n"
        f"• 💥 <b>Live Group Chat Batting:</b> Solo 1-6 · Team 0-6\n"
        f"• 🎬 <b>Animated GIFs & Commentary:</b> Zero-lag replays\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👉 Tap a button below to get started:"
    )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    # Register/fetch user in DB
    await economy_service.get_or_create_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        started_bot=True,
    )

    # Check deep-link parameter (e.g. bowl_{match_id})
    if context.args and len(context.args) > 0 and context.args[0].startswith("bowl_"):
        from .solo import handle_private_bowl_deeplink
        await handle_private_bowl_deeplink(update, context, context.args[0])
        return

    # If in private DM and user is active bowler in a team match
    if str(chat.type) == "private":
        from .team import get_team_bowling_keyboard
        for tm in team_match_service.active_sessions.values():
            if tm.status == "active":
                bowler = tm.get_current_bowler()
                if bowler and bowler.user_id == user.id:
                    striker = tm.get_current_striker()
                    striker_name = striker.name if striker else "Batter"
                    bowler_kb = get_team_bowling_keyboard(tm.match_id)
                    await update.message.reply_html(
                        f"🔔 <b>IT'S YOUR TURN TO BOWL!</b>\n"
                        f"🎯 <b>Match {tm.match_id} • Ball {tm.ball_in_over + 1}/{tm.balls_per_over}</b>\n"
                        f"You are bowling to <b>{striker_name}</b>!\n"
                        "Choose your delivery number (1-6):",
                        reply_markup=bowler_kb
                    )
                    return

        # If in private DM and user is active bowler in a solo match
        from ..services.match_service import match_service
        from ..utils.keyboards import get_bowling_keyboard
        active_match = next(
            (s for s in match_service.active_sessions.values()
             if s.status == "active" and s.current_bowler and s.current_bowler.user_id == user.id),
            None
        )
        if active_match and active_match.current_batter:
            batter = active_match.current_batter
            bowler_kb = get_bowling_keyboard(active_match.match_id)
            ball_num = active_match.current_turn_ball + 1
            await update.message.reply_html(
                f"🎯 <b>Match {active_match.match_id} • Ball {ball_num}/{active_match.balls_per_turn}</b>\n"
                f"You are bowling to <b>{batter.name}</b>!\n"
                "Choose your secret delivery number (1-6):",
                reply_markup=bowler_kb
            )
            return

    text = render_start_text(user.first_name)
    cached_banner = media_cache.get("start_banner")
    if cached_banner and not cached_banner.startswith(("<", "http", "file:")) and update.message:
        try:
            await update.message.reply_photo(
                photo=cached_banner,
                caption=text,
                parse_mode="HTML",
                reply_markup=get_start_keyboard()
            )
            return
        except Exception:
            media_cache.set("start_banner", None)

    banner_path = media_service.get_local_media_path("start_banner")
    if banner_path and os.path.exists(banner_path) and update.message:
        try:
            with open(banner_path, "rb") as f_photo:
                msg = await update.message.reply_photo(
                    photo=f_photo,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=get_start_keyboard()
                )
                if msg and getattr(msg, "photo", None):
                    media_cache.set("start_banner", msg.photo[-1].file_id)
                return
        except Exception as e_p:
            logger.warning(f"Could not send start photo: {e_p}")
    if update.message:
        await update.message.reply_html(text, reply_markup=get_start_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 <b>ULTIMATE CRICKET HELP DESK & PLAYBOOK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Select a topic below for detailed rules, guides, and commands:"
    )
    cached_banner = media_cache.get("start_banner")
    if cached_banner and not cached_banner.startswith(("<", "http", "file:")) and update.message:
        try:
            await update.message.reply_photo(
                photo=cached_banner,
                caption=text,
                parse_mode="HTML",
                reply_markup=get_help_keyboard()
            )
            return
        except Exception:
            media_cache.set("start_banner", None)

    banner_path = media_service.get_local_media_path("start_banner")
    if banner_path and os.path.exists(banner_path) and update.message:
        try:
            with open(banner_path, "rb") as f_photo:
                msg = await update.message.reply_photo(
                    photo=f_photo,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=get_help_keyboard()
                )
                if msg and getattr(msg, "photo", None):
                    media_cache.set("start_banner", msg.photo[-1].file_id)
                return
        except Exception as e_p:
            logger.warning(f"Could not send help photo: {e_p}")
    if update.message:
        await update.message.reply_html(text, reply_markup=get_help_keyboard())

async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all menu_... and help_... callback queries."""
    query = update.callback_query
    if not query:
        return
    data = query.data
    user = update.effective_user

    try:
        await query.answer()
    except Exception:
        pass

    if data == "menu_home":
        await safe_edit_or_reply(query, render_start_text(user.first_name), get_start_keyboard())
        return

    elif data == "menu_play":
        text = (
            "🏏 <b>SELECT MATCH FORMAT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>⚡ Solo Match:</b> Quick multiplayer rotation. Secret DM bowling, group chat batting!\n"
            "• <b>👥 Team Match:</b> Team A vs Team B. Captains, coin toss, overs selection & full team lineup!\n\n"
            "Choose a game mode below to start:"
        )
        await safe_edit_or_reply(query, text, get_match_desk_keyboard())
        return

    elif data == "menu_stats":
        await stats_command(update, context)
        return

    elif data == "menu_leaderboard":
        await leaderboard_command(update, context)
        return

    elif data == "menu_help":
        await safe_edit_or_reply(
            query,
            (
                "📖 <b>ULTIMATE CRICKET HELP DESK & PLAYBOOK</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Select a category below to view detailed rules and guides:"
            ),
            get_help_keyboard()
        )
        return

    elif data == "help_solo":
        text = (
            "⚡ <b>SOLO MULTIPLAYER MODE GUIDE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "1. Start from the Play menu or use <code>/solo</code> in group chat.\n"
            "2. Host chooses delivery format (3 or 6 balls per turn).\n"
            "3. Players join the rotation with <code>/join</code>.\n"
            "4. <b>Bowler Turn:</b> Active bowler receives a private DM from the bot with secret numbers (1-6).\n"
            "5. <b>Batter Turn:</b> Active batter replies with numbers <b>1 to 6</b> in the group chat!\n"
            "6. <b>Wicket:</b> If batter pick == bowler pick → <b>OUT!</b>\n"
            "7. <b>Scoring:</b> If different → batter scores that many runs!\n"
            "8. <b>Timer:</b> 50-second AFK timeout per ball."
        )
        await safe_edit_or_reply(query, text, get_help_keyboard())
        return

    elif data == "help_team":
        text = (
            "👥 <b>TEAM MATCH MODE GUIDE (A to Z)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "1. <b>Lobby:</b> Host starts from the Play menu or uses <code>/create_team</code>.\n"
            "2. <b>Claim Host:</b> Click <b>👑 Claim Host</b>. (Button disappears once claimed!)\n"
            "3. <b>Join Teams:</b> Players join via <b>🔵 Join Team A</b> or <b>🔴 Join Team B</b> buttons.\n"
            "4. <b>Start Match:</b> Host clicks <b>⚡ Start Match</b>.\n"
            "5. <b>Overs:</b> Bot requests overs. Host sends <code>/setovers &lt;number&gt;</code> (1-25).\n"
            "6. <b>Captains:</b> Select Team A & Team B captains via separate buttons.\n"
            "7. <b>Coin Toss:</b> Host sends <code>/toss</code>. Winning captain chooses Bat or Bowl.\n"
            "8. <b>Manual Batter Selection:</b> Batting captain sends <code>/batting</code>, then <code>/batting &lt;number&gt;</code> to send striker.\n"
            "9. <b>Manual Bowler Selection:</b> Bowling captain sends <code>/bowling</code>, then <code>/bowling &lt;number&gt;</code> to choose bowler.\n"
            "10. <b>Gameplay:</b> Bowler picks 1-6 privately in DM; Team batter picks 0-6 in group chat.\n"
            "11. <b>Force Add:</b> Host can add players anytime with <code>/forceadd a @user</code> or <code>/forceadd b @user</code>!"
        )
        await safe_edit_or_reply(query, text, get_help_keyboard())
        return

    elif data == "help_captains":
        text = (
            "👑 <b>HOST & CAPTAIN POWERS & COMMANDS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <code>/setovers &lt;n&gt;</code> - Host sets match overs (1-25)\n"
            "• <code>/toss</code> - Host flips the coin\n"
            "• <code>/batting</code> - Captain/Host views available batters\n"
            "• <code>/batting &lt;n&gt;</code> - Captain/Host sends player to crease\n"
            "• <code>/bowling</code> - Captain/Host views bowling attack\n"
            "• <code>/bowling &lt;n&gt;</code> - Captain/Host selects bowler for over\n"
            "• <code>/forceadd [a/b] @user</code> - Host adds player during match\n"
            "• <code>/changecap [a/b]</code> - Host/Captains rotate team captain\n"
            "• <code>/changehost</code> - Host transfers Game Master role\n"
            "• <code>/spamfree</code> - Host, captains, or bot owner toggle repeat-delivery protection\n"
            "• <code>/penalty +6</code> / <code>/penalty -5</code> - Host/Owner add or cut runs (reply to a player or add a/b)\n"
            "• <code>/rebat &lt;n&gt;</code> - Host/Owner/Batting Captain bring an OUT batter back to bat (or reply to the player)\n"
            "• <code>/endmatch</code> - Host terminates active match immediately"
        )
        await safe_edit_or_reply(query, text, get_help_keyboard())
        return

    elif data == "help_commands":
        text = (
            "📋 <b>ALL COMMANDS DIRECTORY</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>Match Commands:</b>\n"
            "• <code>/create_team</code> - Create a Team match lobby\n"
            "• <code>/solo</code> - Quick solo match\n"
            "• <code>/team</code>, <code>/teams</code> - Team sheet & live score\n"
            "• <code>/score</code> - Live scoreboard\n"
            "• <code>/setovers &lt;n&gt;</code> - Configure match overs\n"
            "• <code>/toss</code> - Conduct coin flip\n"
            "• <code>/batting [n]</code> - Choose active batter\n"
            "• <code>/bowling [n]</code> - Choose active bowler\n"
            "• <code>/forceadd [a/b] @user</code> - Force add player\n"
            "• <code>/endmatch</code> - Cancel match\n\n"
            "<b>Career & Rankings:</b>\n"
            "• <code>/mystats</code> - Career stats profile\n"
            "• <code>/leaderboard</code> - Top 20 Hall of Fame rankings"
        )
        await safe_edit_or_reply(query, text, get_help_keyboard())
        return
