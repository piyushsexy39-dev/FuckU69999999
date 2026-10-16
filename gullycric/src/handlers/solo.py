import os
import io
import time
import json
import html
import asyncio
import tempfile
import logging
from typing import Optional, Dict, Any, Union
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from ..utils import emojis
from ..services.match_service import match_service, MatchSession
from ..services.permissions import permission_service
from ..services.media_service import media_service
from ..services.media_cache import media_cache
from ..services.commentary import get_hindi_commentary, get_hindi_delivery_commentary
from ..engine.match_engine import PlayerSessionStats
from ..utils.keyboards import (
    get_bowling_keyboard,
)

logger = logging.getLogger("gullycric.solo")

def _resolve_milestone_video_path() -> str:
    search_dirs = [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets", "media"),
        os.getcwd(),
    ]
    for directory in search_dirs:
        curr = directory
        for _ in range(5):
            candidate = os.path.join(curr, "milestone_celebration.mp4")
            if os.path.isfile(candidate):
                return candidate
            parent = os.path.dirname(curr)
            if parent == curr:
                break
            curr = parent
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "milestone_celebration.mp4")

MILESTONE_VIDEO_PATH = _resolve_milestone_video_path()
MILESTONE_VIDEO_URL = "https://example.com/milestone_celebration.mp4"


async def send_run_milestone_celebration(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    player: Any,
    milestone: int,
):
    player_mention = (
        f'<a href="tg://user?id={player.user_id}">'
        f"{html.escape(player.name)}</a>"
    )
    if milestone == 50:
        text = (
            f"🎉🔥 HALF-CENTURY! 🏏💯\n"
            f"👏 Congratulations, {player_mention}! What a brilliant knock! 💥\n"
            f"⭐ 50 RUNS COMPLETED! Keep smashing it! 🔥🏆"
        )
    else:
        text = (
            f"🎆👑 CENTURY! 💯🏏\n"
            f"🔥 WHAT AN INCREDIBLE KNOCK, {player_mention}! 👏\n"
            f"💯 100 RUNS COMPLETED! ABSOLUTE LEGEND! 🏆🔥"
        )
    cached_id = media_cache.get("milestone_celebration")
    if cached_id and not cached_id.startswith(("<", "http", "file:")):
        try:
            await context.bot.send_video(
                chat_id=chat_id,
                video=cached_id,
                caption=text,
                parse_mode="HTML",
            )
            return
        except Exception:
            media_cache.set("milestone_celebration", None)

    if callable(getattr(getattr(context.bot, "send_video", None), "side_effect", None)):
        video = MILESTONE_VIDEO_URL
    else:
        video = open(MILESTONE_VIDEO_PATH, "rb") if os.path.isfile(MILESTONE_VIDEO_PATH) else MILESTONE_VIDEO_URL
    try:
        if hasattr(video, "read"):
            with video:
                msg = await context.bot.send_video(
                    chat_id=chat_id,
                    video=video,
                    caption=text,
                    parse_mode="HTML",
                )
                if msg and getattr(msg, "video", None):
                    media_cache.set("milestone_celebration", msg.video.file_id)
        else:
            await context.bot.send_video(
                chat_id=chat_id,
                video=video,
                caption=text,
                parse_mode="HTML",
            )
    except OSError as error:
        logger.warning("Could not open milestone video %s: %s", MILESTONE_VIDEO_PATH, error)


async def send_run_milestone_celebrations(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    player: Any,
    previous_runs: int,
    innings: int,
    celebrated_milestones: set[tuple[int, int, int]],
):
    for milestone in (50, 100):
        celebration_key = (innings, player.user_id, milestone)
        if (
            previous_runs < milestone <= player.runs
            and celebration_key not in celebrated_milestones
        ):
            await send_run_milestone_celebration(
                context, chat_id, player, milestone
            )
            celebrated_milestones.add(celebration_key)


def mention(target: Any) -> str:
    """Returns a clickable mention in HTML: @username if available, or tg://user?id link."""
    if not target:
        return "Player"
    username = getattr(target, "username", None)
    if username:
        return f"@{username}"
    uid = getattr(target, "user_id", getattr(target, "id", None))
    raw_name = getattr(target, "name", getattr(target, "first_name", "Player"))
    name = html.escape(str(raw_name))
    if uid:
        return f'<a href="tg://user?id={uid}">{name}</a>'
    return f"<b>{name}</b>"

def get_pulse_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    dm_url = f"https://t.me/{bot_username}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{emojis.BALL} Open Bowling DM", url=dm_url)]
    ])

def get_post_match_keyboard(bot_username: str = "UltimateCricket_BOT") -> InlineKeyboardMarkup:
    clean_username = bot_username.replace("@", "") if bot_username else "UltimateCricket_BOT"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"PLAY AGAIN {emojis.REFRESH}", callback_data="select_solo_mode")],
        [InlineKeyboardButton("ADD IN GROUP ➕", url=f"https://t.me/{clean_username}?startgroup=true")]
    ])


def should_reject_spamfree_pick(session: Any, bowler_id: int, pick: int) -> bool:
    return bool(
        session.spamfree
        and session.spamfree_last_bowler_id == bowler_id
        and session.spamfree_last_bowler_pick == pick
        and session.spamfree_consecutive_count >= 2
    )


def record_spamfree_pick(session: Any, bowler_id: int, pick: int):
    if not session.spamfree:
        return
    if (
        session.spamfree_last_bowler_id == bowler_id
        and session.spamfree_last_bowler_pick == pick
    ):
        session.spamfree_consecutive_count += 1
    else:
        session.spamfree_last_bowler_id = bowler_id
        session.spamfree_last_bowler_pick = pick
        session.spamfree_consecutive_count = 1


def reset_spamfree_sequence(session: Any):
    session.spamfree_last_bowler_id = None
    session.spamfree_last_bowler_pick = None
    session.spamfree_consecutive_count = 0

async def safe_edit_or_reply(target: Any, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    query = getattr(target, "callback_query", target if hasattr(target, "edit_message_text") else None)
    if query:
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
            return
        except Exception:
            pass
        try:
            await query.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=reply_markup)
            return
        except Exception:
            pass
        try:
            if hasattr(query, "message") and query.message:
                await query.message.reply_html(text, reply_markup=reply_markup)
                return
        except Exception:
            pass
    if hasattr(target, "message") and target.message:
        try:
            await target.message.reply_html(text, reply_markup=reply_markup)
        except Exception:
            pass
    elif hasattr(target, "effective_message") and target.effective_message:
        try:
            await target.effective_message.reply_html(text, reply_markup=reply_markup)
        except Exception:
            pass

def _build_lobby_text(session: MatchSession, listed: int) -> str:
    lines = [
        f"{emojis.BAT} <b>ULTIMATE CRICKET • SOLO MATCH LOBBY</b>",
        f"{emojis.CROWN} <b>Host:</b> {html.escape(str(session.host_name))}",
        f"{emojis.LIGHTNING} <b>Format:</b> Unlimited Overs · {session.balls_per_turn} Balls/Turn\n",
        f"👥 <b>Players in queue ({len(session.players)}):</b>"
    ]
    for idx, p in enumerate(session.players[:listed], start=1):
        tag = " (Host)" if p.user_id == session.host_id else ""
        lines.append(f"{idx}. <b>{html.escape(str(p.name))}</b>{tag}")
    hidden = len(session.players) - listed
    if hidden > 0:
        lines.append(f"<i>…and {hidden} more</i>")

    if len(session.players) == 1:
        lines.append("2. <i>Waiting for opponent to join...</i>\n")
    else:
        lines.append(f"\n⏳ <b>Starting in:</b> <code>{session.countdown_seconds}s</code>\n")

    lines.append(
        "📝 <b>Match Commands:</b>\n"
        "• <code>/joinsolo</code> - Join the match\n"
        "• <code>/leavesolo</code> - Leave before match starts\n"
        "• <code>/extend</code> - Extend 30s to join\n"
        "• <code>/forcestart</code> - Start match immediately (Host/Admin)\n"
        "• <code>/endsolo</code> - End match anytime (Host/Admin)"
    )
    return "\n".join(lines)


def render_lobby_text(session: MatchSession) -> str:
    # Captions (photo/video messages) are limited to 1024 chars: shrink the player list if needed.
    limit = 1000 if getattr(session, "lobby_has_media", False) else 4000
    listed = len(session.players)
    text = _build_lobby_text(session, listed)
    while len(text) > limit and listed > 3:
        listed -= 1
        text = _build_lobby_text(session, listed)
    return text


def render_started_text(session: MatchSession) -> str:
    limit = 1000 if getattr(session, "lobby_has_media", False) else 4000

    def build(listed: int) -> str:
        lines = [
            "🏏 <b>ULTIMATE CRICKET • SOLO ARENA</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "🔥 <b>THE MATCH HAS BEGUN!</b> 🔥",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"{emojis.CROWN} <b>Host:</b> {html.escape(str(session.host_name))}",
            f"{emojis.LIGHTNING} <b>Format:</b> Unlimited Overs · {session.balls_per_turn} Balls/Turn",
            "",
            f"⚔️ <b>Players in the arena ({len(session.players)}):</b>",
        ]
        for idx, p in enumerate(session.players[:listed]):
            if idx == session.current_batter_idx:
                icon, note = "🏏", " · <i>batting first</i>"
            elif idx == session.current_bowler_idx:
                icon, note = "🎯", " · <i>bowling first</i>"
            else:
                icon, note = "⏳", ""
            lines.append(f"{icon} <b>{html.escape(str(p.name))}</b>{note}")
        hidden = len(session.players) - listed
        if hidden > 0:
            lines.append(f"<i>…and {hidden} more</i>")
        lines += [
            "━━━━━━━━━━━━━━━━━━━━━━",
            "🔒 <i>Lobby closed. Let the battle begin — may the best player win!</i>",
        ]
        return "\n".join(lines)

    listed = len(session.players)
    text = build(listed)
    while len(text) > limit and listed > 3:
        listed -= 1
        text = build(listed)
    return text


_lobby_locks: Dict[str, asyncio.Lock] = {}


async def _edit_lobby_message(context: ContextTypes.DEFAULT_TYPE, session: MatchSession, render) -> bool:
    """Edit the tracked lobby message (caption if it is a media message). Returns True on success."""
    mid = getattr(session, "lobby_message_id", None)
    if not mid:
        return False
    lock = _lobby_locks.setdefault(session.match_id, asyncio.Lock())
    async with lock:
        text = render(session)  # rendered inside the lock so rapid joins never show stale lists
        try:
            if session.lobby_has_media:
                await context.bot.edit_message_caption(
                    chat_id=session.chat_id, message_id=mid, caption=text, parse_mode="HTML"
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=session.chat_id, message_id=mid, text=text, parse_mode="HTML"
                )
            return True
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return True
            logger.warning("Could not edit solo lobby message %s: %s", mid, e)
        except Exception as e:
            logger.warning("Could not edit solo lobby message %s: %s", mid, e)
    return False


async def refresh_lobby_message(context: ContextTypes.DEFAULT_TYPE, session: MatchSession) -> None:
    await _edit_lobby_message(context, session, render_lobby_text)


async def unpin_lobby_message(context: ContextTypes.DEFAULT_TYPE, session: MatchSession) -> None:
    if getattr(session, "lobby_pinned", False) and getattr(session, "lobby_message_id", None):
        session.lobby_pinned = False
        try:
            await context.bot.unpin_chat_message(chat_id=session.chat_id, message_id=session.lobby_message_id)
        except Exception as e:
            logger.warning("Could not unpin solo lobby message: %s", e)
    _lobby_locks.pop(getattr(session, "match_id", ""), None)


async def close_lobby_message(context: ContextTypes.DEFAULT_TYPE, session: MatchSession, text: str) -> None:
    await _edit_lobby_message(context, session, lambda _s: text)
    await unpin_lobby_message(context, session)


def render_solo_lineup(session: MatchSession) -> str:
    batting_lines = []
    for index, player in enumerate(session.players, start=1):
        if player is session.current_batter:
            role = " · CURRENT BATTER"
        elif player.is_out:
            role = " · OUT"
        elif player.has_batted:
            role = " · COMPLETED"
        else:
            role = " · TO BAT"
        batting_lines.append(f"{index}. <b>{html.escape(str(player.name))}</b>{role}")

    bowler_order = []
    player_count = len(session.players)
    if player_count:
        position = 0
        for offset in range(player_count):
            index = (session.current_bowler_idx + offset) % player_count
            if index == session.current_batter_idx:
                continue
            player = session.players[index]
            position += 1
            role = " · CURRENT BOWLER" if player is session.current_bowler else ""
            bowler_order.append(
                f"{position}. <b>{html.escape(str(player.name))}</b>{role}"
            )

    return (
        f"🏏 <b>SOLO LINEUP · {len(session.players)} PLAYERS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Batting order</b>\n" + "\n".join(batting_lines) + "\n\n"
        f"<b>Bowling order (current rotation)</b>\n"
        + ("\n".join(bowler_order) if bowler_order else "No eligible bowler")
    )

def render_scorecard(session: MatchSession, is_final: bool = False) -> str:
    status_header = "🏆 <b>ULTIMATE CRICKET • FINAL SCOREBOARD</b>" if is_final else "🏏 <b>LIVE MATCH SCOREBOARD</b>"
    lines = [
        status_header,
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Match ID: <code>{session.match_id}</code> · Format: Unlimited Overs ({session.balls_per_turn}b/turn)\n"
    ]
    if not is_final and session.current_batter and session.current_bowler:
        b = session.current_batter
        bw = session.current_bowler
        lines.append(f"🏏 <b>Striker:</b> {mention(b)} · <b>{b.runs}*</b> ({b.balls}b)")
        lines.append(f"🎯 <b>Bowler:</b> {mention(bw)} · Ball <b>{session.current_turn_ball}/{session.balls_per_turn}</b>\n")

    sorted_players = sorted(
        session.players,
        key=lambda p: (p.runs, p.strike_rate, p.wickets, -p.runs_conceded),
        reverse=True
    )
    rank_icons = ["1.", "2.", "3.", "4.", "5."]

    for idx, p in enumerate(sorted_players, start=1):
        num_prefix = rank_icons[idx - 1] if idx <= len(rank_icons) else f"{idx}."
        crown = " 👑" if (is_final and idx == 1 and (len(sorted_players) == 1 or sorted_players[0].runs > sorted_players[1].runs)) else ""

        status_tag = ""
        if not is_final:
            if session.current_batter and p.user_id == session.current_batter.user_id:
                status_tag = " 🏏 <i>[Batting*]</i>"
            elif p.is_out:
                status_tag = " ❌ <i>[OUT]</i>"
            elif p.has_batted:
                status_tag = " 🏁 <i>[Finished]</i>"
            else:
                status_tag = " ⏳ <i>[Waiting]</i>"

        p_name = html.escape(str(p.name))
        lines.append(f"{num_prefix} <b>{p_name}</b>{crown}{status_tag}")
        lines.append(f"🏏 <b>{p.runs}</b> ({p.balls}b) · SR <b>{p.strike_rate:.1f}</b>")
        lines.append(f"↳ 4s: <b>{p.fours}</b> · 6s: <b>{p.sixes}</b>")
        lines.append(f"⚾ <b>{p.wickets}/{p.runs_conceded}</b> · <b>{p.overs_bowled}</b> ov · Eco <b>{p.economy:.2f}</b>\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    if is_final:
        w_name = html.escape(str(sorted_players[0].name)) if sorted_players else "Winner"
        if len(sorted_players) > 1 and sorted_players[0].runs == sorted_players[1].runs:
            if sorted_players[0].strike_rate > sorted_players[1].strike_rate:
                lines.append(f"🏆 <b>WINNER: {w_name}</b> (Higher Strike Rate: {sorted_players[0].strike_rate:.1f})")
            elif (sorted_players[0].sixes + sorted_players[0].fours) > (sorted_players[1].sixes + sorted_players[1].fours):
                lines.append(f"🏆 <b>WINNER: {w_name}</b> (Boundary count advantage)")
            elif sorted_players[0].wickets > sorted_players[1].wickets:
                lines.append(f"🏆 <b>WINNER: {w_name}</b> (Most wickets taken)")
            else:
                if session.winner_name:
                    lines.append(f"🏆 <b>WINNER: {html.escape(str(session.winner_name))}</b> ({session.result_summary})")
                else:
                    lines.append(f"🏆 <b>WINNER: {w_name}</b> (Scoreboard tiebreaker)")
        elif len(sorted_players) > 0:
            lines.append(f"🏆 <b>WINNER: {w_name}</b> with <b>{sorted_players[0].runs}</b> runs!")
        lines.append("🏁 <i>Match complete — tap Play Again for another innings.</i>")

    return "\n".join(lines).strip()

def calculate_potm(session: MatchSession) -> PlayerSessionStats:
    """Calculates Player of the Match based on composite cricket impact."""
    def potm_score(p: PlayerSessionStats) -> float:
        return (
            p.runs * 1.5
            + p.fours * 2.0
            + p.sixes * 4.0
            + p.wickets * 25.0
            - p.runs_conceded * 0.5
            + (p.strike_rate * 0.05)
        )
    return max(session.players, key=potm_score)

def generate_potm_caption(potm: PlayerSessionStats) -> str:
    if potm.wickets >= 1 and potm.runs >= 10:
        tagline = "Pure class from start to finish. 🌟"
    elif potm.sixes >= 2:
        tagline = "Destructive boundary hitting! Pure entertainment. 💥"
    elif potm.wickets >= 2:
        tagline = "Lethal bowling spell! Tore through the batting order. 🎯"
    elif potm.strike_rate >= 200:
        tagline = "Electrifying strike rate and clinical finishing! ⚡"
    else:
        tagline = "Pure class from start to finish. 🌟"

    p_name = html.escape(str(potm.name))
    return (
        f"🎖️ <b>PLAYER OF THE MATCH</b>\n\n"
        f"<b>{p_name}</b> — <b>{potm.runs}</b> ({potm.balls}b) · <b>{potm.wickets}/{potm.runs_conceded}</b> ({potm.overs_bowled} ov)\n"
        f"<i>{tagline}</i>"
    )

async def send_potm_with_pfp(context: ContextTypes.DEFAULT_TYPE, chat_id: int, potm_user_id: int, potm_caption: str):
    pfp_sent = False
    if potm_user_id:
        try:
            photos = await context.bot.get_user_profile_photos(user_id=potm_user_id, limit=1)
            if photos and photos.total_count > 0:
                file_id = photos.photos[0][0].file_id
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=file_id,
                    caption=potm_caption,
                    parse_mode="HTML"
                )
                pfp_sent = True
        except Exception:
            pass

    if not pfp_sent:
        potm_banner = media_service.get_local_media_path("potm")
        if potm_banner and os.path.exists(potm_banner):
            try:
                with open(potm_banner, "rb") as f_potm:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=f_potm,
                        caption=potm_caption,
                        parse_mode="HTML"
                    )
                    pfp_sent = True
            except Exception:
                pass

    if not pfp_sent:
        await context.bot.send_message(
            chat_id=chat_id,
            text=potm_caption,
            parse_mode="HTML"
        )

async def send_match_completion_sequence(context: ContextTypes.DEFAULT_TYPE, session: MatchSession):
    await unpin_lobby_message(context, session)
    bot_info = await context.bot.get_me()
    scorecard_text = render_scorecard(session, is_final=True)
    reply_kb = get_post_match_keyboard(bot_info.username)

    # 1. Send Final Scoreboard with banner photo if available
    banner_path = media_service.get_local_media_path("scorecard")
    photo_sent = False
    if banner_path and os.path.exists(banner_path):
        try:
            if len(scorecard_text) <= 1024:
                with open(banner_path, "rb") as f_photo:
                    await context.bot.send_photo(
                        chat_id=session.chat_id,
                        photo=f_photo,
                        caption=scorecard_text,
                        parse_mode="HTML",
                        reply_markup=reply_kb
                    )
                    photo_sent = True
            else:
                with open(banner_path, "rb") as f_photo:
                    await context.bot.send_photo(
                        chat_id=session.chat_id,
                        photo=f_photo,
                        caption="🏆 <b>FINAL SCOREBOARD</b>",
                        parse_mode="HTML"
                    )
        except Exception as e_sc:
            logger.warning(f"Could not send scoreboard photo: {e_sc}")

    if not photo_sent:
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=scorecard_text,
            parse_mode="HTML",
            reply_markup=reply_kb
        )

    # 2. Determine and Send POTM with user profile picture
    if not session.players:
        return

    potm = calculate_potm(session)
    potm_caption = generate_potm_caption(potm)
    await send_potm_with_pfp(context, session.chat_id, potm.user_id, potm_caption)

def render_over_summary(data: Dict[str, Any]) -> str:
    bowler_name = html.escape(str(data.get("bowler_name", "Bowler")))
    batter_name = html.escape(str(data.get("batter_name", "Batter")))
    runs = data.get("runs", 0)
    wkts = data.get("wkts", 0)
    balls = data.get("balls", [])
    ball_by_ball_str = " • ".join(str(b) for b in balls) if balls else "-"
    batter_runs = data.get("batter_runs", 0)
    batter_balls = data.get("batter_balls", 0)
    batter_sr = data.get("batter_sr", 0.0)

    return (
        f"✅ <b>OVER SUMMARY</b>\n"
        f"🎾 <b>{bowler_name}</b> to <b>{batter_name}</b>\n\n"
        f"📊 <b>THIS OVER</b>\n"
        f"├─ Bowler: <b>{bowler_name}</b>\n"
        f"├─ Runs: <b>{runs} R</b>\n"
        f"├─ Wkts: <b>{wkts} W</b>\n"
        f"└─ Ball-by-Ball: [ <b>{ball_by_ball_str}</b> ]\n\n"
        f"🏏 <b>BATTERS</b>\n"
        f"└─ 🟢 <b>{batter_name}</b>: {batter_runs} ({batter_balls}) • SR {batter_sr:.1f}"
    )

def cancel_turn_timer(session: MatchSession):
    task = session.turn_timer_task
    if task and not task.done():
        # The AFK timers call this while finishing a ball/match. Cancelling the
        # task that is currently running would abort it before the scoreboard,
        # poster and Player of the Match are sent, so never cancel ourselves.
        try:
            is_self = task is asyncio.current_task()
        except RuntimeError:
            is_self = False
        if not is_self:
            task.cancel()
    session.turn_timer_task = None

def start_bowler_timer(context: ContextTypes.DEFAULT_TYPE, session: MatchSession):
    cancel_turn_timer(session)
    session.turn_timer_task = asyncio.create_task(run_bowler_turn_timer(context, session))

def start_batter_timer(context: ContextTypes.DEFAULT_TYPE, session: MatchSession):
    cancel_turn_timer(session)
    session.turn_timer_task = asyncio.create_task(run_batter_turn_timer(context, session))

async def run_bowler_turn_timer(context: ContextTypes.DEFAULT_TYPE, session: MatchSession):
    """Counts down for bowler AFK (50s total with 30s and 20s warnings)."""
    bowler = session.current_bowler
    batter = session.current_batter
    if not bowler or not batter:
        return
    try:
        # 20s passed -> 30s remaining warning
        await asyncio.sleep(20)
        if (
            session.status != "active"
            or session.pending_bowler_pick is not None
            or session.current_bowler is not bowler
            or session.current_batter is not batter
        ):
            return
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"⏳ <b>30s remaining!</b>\n"
                f"🎯 <b>Bowling:</b> {mention(bowler)}\n"
                f"🏏 <b>Batting:</b> {html.escape(batter.name)}\n"
                f"👉 {mention(bowler)}, please open your private DM and choose your delivery (1-6)!"
            ),
            parse_mode="HTML"
        )

        # 10s more passed (30s total) -> 20s remaining warning
        await asyncio.sleep(10)
        if (
            session.status != "active"
            or session.pending_bowler_pick is not None
            or session.current_bowler is not bowler
            or session.current_batter is not batter
        ):
            return
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"⚠️ <b>20s remaining!</b>\n"
                f"🎯 <b>Bowling:</b> {mention(bowler)} | 🏏 <b>Batting:</b> {html.escape(batter.name)}\n"
                f"👉 {mention(bowler)}, hurry up and bowl or your turn will time out!"
            ),
            parse_mode="HTML"
        )

        # 20s more passed (50s total) -> Timeout
        await asyncio.sleep(20)
        if (
            session.status != "active"
            or session.pending_bowler_pick is not None
            or session.current_bowler is not bowler
            or session.current_batter is not batter
        ):
            return

        res = await match_service.handle_bowler_afk_timeout(session.match_id)
        if res.get("status") == "match_won_by_afk":
            winner = res["winner"]
            afk_player = res["afk_player"]
            reason = res.get("reason", "")
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=(
                    f"⏱️ <b>TIME OUT!</b> {mention(afk_player)} was AFK and failed to bowl within 50 seconds!\n\n"
                    f"🏆 <b>WINNER:</b> {mention(winner)}!\n"
                    f"📊 <i>{reason}</i>"
                ),
                parse_mode="HTML"
            )
            await send_match_completion_sequence(context, session)
            return

        if res.get("status") == "bowler_changed":
            new_bw = res["new_bowler"]
            old_bw = res["old_bowler"]
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=(
                    f"⚠️ {mention(old_bw)} was AFK and timed out after 50 seconds!\n"
                    f"🔄 <b>Bowler changed automatically to:</b> {mention(new_bw)}!\n"
                    f"🏏 <b>Batting:</b> {mention(session.current_batter)}\n"
                    f"🎯 <b>Bowling:</b> {mention(new_bw)}"
                ),
                parse_mode="HTML"
            )
            try:
                bowler_kb = get_bowling_keyboard(session.match_id)
                await context.bot.send_message(
                    chat_id=new_bw.user_id,
                    text=(
                        f"🔔 <b>IT'S YOUR TURN TO BOWL!</b>\n"
                        f"{emojis.TARGET} <b>Match {session.match_id} • Ball {session.current_turn_ball + 1}/{session.balls_per_turn}</b>\n"
                        f"You are bowling to <b>{session.current_batter.name}</b>!\n"
                        "Choose your delivery number (1-6):"
                    ),
                    parse_mode="HTML",
                    reply_markup=bowler_kb
                )
            except Exception:
                pass
            start_bowler_timer(context, session)

    except asyncio.CancelledError:
        pass

async def run_batter_turn_timer(context: ContextTypes.DEFAULT_TYPE, session: MatchSession):
    """Counts down for batter AFK (50s total with 30s and 20s warnings)."""
    batter = session.current_batter
    bowler = session.current_bowler
    if not batter or not bowler:
        return
    try:
        # 20s passed -> 30s remaining warning
        await asyncio.sleep(20)
        if (
            session.status != "active"
            or session.pending_batter_pick is not None
            or session.current_batter is not batter
            or session.current_bowler is not bowler
        ):
            return
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"⏳ <b>30s remaining!</b>\n"
                f"🏏 <b>Batting:</b> {mention(batter)}\n"
                f"🎯 <b>Bowling:</b> {html.escape(bowler.name)}\n"
                f"👉 {mention(batter)}, play your shot (reply 1-6 in this chat)!"
            ),
            parse_mode="HTML"
        )

        # 10s more passed (30s total) -> 20s remaining warning
        await asyncio.sleep(10)
        if (
            session.status != "active"
            or session.pending_batter_pick is not None
            or session.current_batter is not batter
            or session.current_bowler is not bowler
        ):
            return
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"⚠️ <b>20s remaining!</b>\n"
                f"🏏 <b>Batting:</b> {mention(batter)} | 🎯 <b>Bowling:</b> {html.escape(bowler.name)}\n"
                f"👉 {mention(batter)}, swing your bat now or you will be declared OUT!"
            ),
            parse_mode="HTML"
        )

        # 20s more passed (50s total) -> Timeout
        await asyncio.sleep(20)
        if (
            session.status != "active"
            or session.pending_batter_pick is not None
            or session.current_batter is not batter
            or session.current_bowler is not bowler
        ):
            return

        res = await match_service.handle_batter_afk_timeout(session.match_id)
        if res.get("status") == "match_won_by_afk":
            winner = res["winner"]
            afk_player = res["afk_player"]
            reason = res.get("reason", "")
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=(
                    f"⏱️ <b>TIME OUT!</b> {mention(afk_player)} was AFK and took more than 50 seconds to bat!\n\n"
                    f"🏆 <b>WINNER:</b> {mention(winner)}!\n"
                    f"📊 <i>{reason}</i>"
                ),
                parse_mode="HTML"
            )
            await send_match_completion_sequence(context, session)
            return

        if res.get("status") == "ball_resolved":
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=(
                    f"⏱️ <b>TIMED OUT!</b> {mention(batter)} took more than 50 seconds to play and is declared <b>OUT!</b>\n"
                    f"🏏 <b>Batting:</b> {mention(batter)} | 🎯 <b>Bowling:</b> {mention(bowler)}"
                ),
                parse_mode="HTML"
            )
            await update_group_after_delivery(context, session.match_id, res)

    except asyncio.CancelledError:
        pass

async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    chat_type = str(getattr(chat, "type", "private")).lower()
    if chat and ("group" in chat_type or "supergroup" in chat_type):
        from .start import get_match_desk_keyboard
        text = (
            "🏏 <b>SELECT MATCH FORMAT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>⚡ Solo Match:</b> Quick multiplayer rotation. Secret DM bowling, group chat batting!\n"
            "• <b>👥 Team Match:</b> Team A vs Team B. Captains, coin toss, overs selection & full team lineup!\n\n"
            "Choose a game mode below to start:"
        )
        if update.message:
            banner_path = media_service.get_local_media_path("start_banner")
            if banner_path and os.path.exists(banner_path):
                try:
                    with open(banner_path, "rb") as f_photo:
                        await update.message.reply_photo(
                            photo=f_photo,
                            caption=text,
                            parse_mode="HTML",
                            reply_markup=get_match_desk_keyboard()
                        )
                        return
                except Exception as e_p:
                    logger.warning(f"Could not send game banner: {e_p}")
            await update.message.reply_html(text, reply_markup=get_match_desk_keyboard())
        return
    from .start import start_command
    await start_command(update, context)

async def solo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    from ..services.team_match_service import team_match_service
    team_session = team_match_service.get_session(chat.id)
    if team_session and team_session.status in (
        "lobby", "awaiting_overs", "choosing_captains", "awaiting_toss",
        "toss_call", "toss_decision", "active",
    ):
        await update.message.reply_html(
            "⚠️ A match is already in progress in this group. Finish or cancel it before starting another."
        )
        return

    # Check if a game is already running or in lobby in this chat
    existing = match_service.get_active_session_for_chat(chat.id)
    if existing:
        if existing.status == "active":
            await update.message.reply_html(
                f"⚠️ <b>A match is already in progress in this chat!</b> (ID: <code>{existing.match_id}</code>)\n"
                f"🏏 <b>Batter:</b> {mention(existing.current_batter)}\n"
                f"🎯 <b>Bowler:</b> {mention(existing.current_bowler)}\n"
                f"Use <code>/score</code> to view scoreboard or <code>/endsolo</code> to conclude it."
            )
            return
        elif existing.status in ("lobby", "countdown"):
            text = render_lobby_text(existing)
            await safe_edit_or_reply(update, text, reply_markup=None)
            return

    # Show balls selection prompt: 3 or 6 balls
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ 3 Balls / Turn", callback_data=f"set_balls_{user.id}_3"),
            InlineKeyboardButton("🎯 6 Balls / Turn", callback_data=f"set_balls_{user.id}_6")
        ]
    ])
    text = (
        f"🏏 <b>ULTIMATE CRICKET • SOLO SETUP</b>\n"
        f"👑 Host: <b>{user.first_name}</b>\n\n"
        f"Choose delivery format per turn:"
    )
    await safe_edit_or_reply(update, text, kb)

async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    # 1. Mid-game join request
    active_match = next(
        (s for s in match_service.active_sessions.values() if s.chat_id == chat.id and s.status == "active"),
        None
    )
    if active_match:
        if any(p.user_id == user.id for p in active_match.players):
            await update.message.reply_html("ℹ️ You are already in the match!")
            return

        active_match.pending_join_requests[user.id] = user.first_name
        req_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"{emojis.CHECK} Accept {user.first_name}", callback_data=f"accept_mid_{active_match.match_id}_{user.id}"),
                InlineKeyboardButton(f"{emojis.CROSS} Deny", callback_data=f"deny_mid_{active_match.match_id}_{user.id}")
            ]
        ])
        await update.message.reply_html(
            f"🙋‍♂️ <b>{user.first_name}</b> wants to join the ongoing match rotation!\n"
            f"👑 Host ({active_match.host_name}), do you accept?",
            reply_markup=req_kb
        )
        return

    # 2. Lobby join
    active_lobby = next(
        (s for s in match_service.active_sessions.values() if s.chat_id == chat.id and s.status in ("lobby", "countdown")),
        None
    )
    if not active_lobby:
        await update.message.reply_html(
            f"{emojis.CROSS} No open solo lobby found in this chat!\n"
            f"Use the Play menu or <code>/solo</code> to create one."
        )
        return

    if any(p.user_id == user.id for p in active_lobby.players):
        await update.message.reply_html("ℹ️ You are already in the solo queue!")
        return

    joined = await match_service.join_solo_lobby(active_lobby.match_id, user.id, user.first_name)
    if not joined:
        await update.message.reply_html(f"{emojis.CROSS} Could not join lobby.")
        return

    if len(joined.players) == 2 and not joined.countdown_task:
        joined.countdown_seconds = 30
        joined.countdown_task = asyncio.create_task(run_lobby_countdown(context, joined))

    await update.message.reply_html(
        f"✅ <b>{user.first_name}</b> joined the solo queue!"
    )
    await refresh_lobby_message(context, joined)

async def run_lobby_countdown(context: ContextTypes.DEFAULT_TYPE, session: MatchSession):
    try:
        while session.countdown_seconds > 0 and session.status == "countdown":
            if session.countdown_seconds in (30, 20, 10):
                await context.bot.send_message(
                    chat_id=session.chat_id,
                    text=f"⏳ <b>Solo Match starting in {session.countdown_seconds}s!</b>\nJoin with <code>/joinsolo</code>!",
                    parse_mode="HTML"
                )
                await refresh_lobby_message(context, session)
            await asyncio.sleep(5)
            session.countdown_seconds -= 5

        if session.status == "countdown" and len(session.players) >= 2:
            await start_match_flow(None, context, session)
    except asyncio.CancelledError:
        pass

async def extend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    session = next(
        (s for s in match_service.active_sessions.values() if s.chat_id == chat.id and s.status in ("lobby", "countdown")),
        None
    )
    if not session:
        await update.message.reply_html(f"{emojis.CROSS} No open lobby to extend in this chat.")
        return

    session.countdown_seconds = getattr(session, "countdown_seconds", 30) + 30
    await update.message.reply_html(
        f"⏳ <b>Joining time extended by 30 seconds!</b>\n"
        f"Time remaining: <code>{session.countdown_seconds}s</code>\n"
        f"Type <code>/joinsolo</code> to enter!"
    )
    await refresh_lobby_message(context, session)

async def leave_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    active = next(
        (s for s in match_service.active_sessions.values() if s.chat_id == chat.id and s.status == "active"),
        None
    )
    if active and any(p.user_id == user.id for p in active.players):
        await update.message.reply_html(f"{emojis.CROSS} Match is live! Host or admin can end with <code>/endsolo</code>.")
        return

    lobby = next(
        (s for s in match_service.active_sessions.values() if s.chat_id == chat.id and s.status in ("lobby", "countdown")),
        None
    )
    if lobby:
        in_p = [p for p in lobby.players if p.user_id == user.id]
        if not in_p:
            await update.message.reply_html("ℹ️ You are not in the solo queue.")
            return

        if user.id == lobby.host_id and len(lobby.players) == 1:
            match_service.active_sessions.pop(lobby.match_id, None)
            await update.message.reply_html(f"🛑 Host <b>{user.first_name}</b> left. Solo lobby cancelled.")
            await close_lobby_message(
                context, lobby,
                f"🛑 <b>SOLO LOBBY CANCELLED</b>\nHost <b>{html.escape(user.first_name)}</b> left the lobby.\nStart a new one with /solo"
            )
            return

        lobby.players = [p for p in lobby.players if p.user_id != user.id]
        await update.message.reply_html(f"👋 <b>{user.first_name}</b> left the solo queue.")
        await refresh_lobby_message(context, lobby)

async def forcestart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    lobby = next(
        (s for s in match_service.active_sessions.values() if s.chat_id == chat.id and s.status in ("lobby", "countdown")),
        None
    )
    if not lobby:
        await update.message.reply_html(f"{emojis.CROSS} No waiting lobby to start in this chat!")
        return

    is_admin = await permission_service.is_chat_admin(context.bot, chat.id, user.id)
    if user.id != lobby.host_id and not is_admin:
        await update.message.reply_html(f"{emojis.CROSS} Only the host or chat admin can force start!")
        return

    if len(lobby.players) < 2:
        await update.message.reply_html(f"{emojis.CROSS} Need at least 2 players in the queue to start! Type <code>/joinsolo</code> to join!")
        return

    if lobby.countdown_task and not lobby.countdown_task.done():
        lobby.countdown_task.cancel()

    await start_match_flow(update, context, lobby)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await endsolo_command(update, context)

async def endsolo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    session = match_service.get_active_session_for_chat(chat.id)
    if not session:
        await update.message.reply_html(f"{emojis.CROSS} No active solo match in this chat right now.")
        return

    is_admin = await permission_service.is_chat_admin(context.bot, chat.id, user.id)
    if user.id != session.host_id and not is_admin:
        await update.message.reply_html(f"{emojis.CROSS} Only the host or chat admin can end the solo match!")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{emojis.CHECK} Yes, End Match", callback_data=f"endsolo_yes_{session.match_id}"),
            InlineKeyboardButton(f"{emojis.CROSS} No, Keep Playing", callback_data=f"endsolo_no_{session.match_id}")
        ]
    ])
    await update.message.reply_html(
        f"{emojis.WARNING} <b>Are you sure you want to end this match?</b>\n"
        "Ending will save match progress to a backup JSON file so you can resume anytime with <code>/restore</code>.",
        reply_markup=keyboard
    )

async def replay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restores and resumes match from replied JSON document."""
    message = update.message
    reply_to = message.reply_to_message if message else None

    # Check replied message or current message
    target_msg = reply_to if (reply_to and reply_to.document) else (message if (message and message.document) else None)
    if not target_msg or not target_msg.document:
        await message.reply_html(
            f"{emojis.CROSS} Please reply to a match backup JSON file with <code>/restore</code> to resume the match!"
        )
        return

    doc = target_msg.document
    if not doc.file_name or not doc.file_name.endswith(".json"):
        await message.reply_html(f"{emojis.CROSS} Please reply to a valid <b>.json</b> match backup file!")
        return

    try:
        file_obj = await context.bot.get_file(doc.file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
            tmp_path = tmp.name
        await file_obj.download_to_drive(tmp_path)
        with open(tmp_path, "r") as tf:
            data = json.load(tf)
        os.remove(tmp_path)

        chat_id = update.effective_chat.id
        from ..services.team_match_service import team_match_service
        team_session = team_match_service.get_session(chat_id)
        if match_service.get_active_session_for_chat(chat_id) or (
            team_session and team_session.status in (
                "lobby", "awaiting_overs", "choosing_captains", "awaiting_toss",
                "toss_call", "toss_decision", "active",
            )
        ):
            raise ValueError("A match is already in progress in this group.")

        if data.get("mode") == "team" or "team_a_players" in data:
            from ..services.team_match_service import team_match_service, TeamMatchSession
            from .team import _start_delivery
            team_session = TeamMatchSession.from_dict(data)
            team_session.chat_id = chat_id
            team_session.status = "active"
            team_match_service.active_sessions[chat_id] = team_session

            striker = team_session.get_current_striker()
            non_striker = team_session.get_current_non_striker()
            bowler = team_session.get_current_bowler()

            bat_name = team_session.team_a_name if team_session.batting_team == "team_a" else team_session.team_b_name
            curr_tot = team_session.total_runs_a if team_session.batting_team == "team_a" else team_session.total_runs_b
            curr_w = team_session.wickets_a if team_session.batting_team == "team_a" else team_session.wickets_b
            curr_b = team_session.balls_bowled_a if team_session.batting_team == "team_a" else team_session.balls_bowled_b
            ov_s = f"{curr_b // team_session.balls_per_over}.{curr_b % team_session.balls_per_over}"
            tgt_s = f" • Target: {team_session.target + 1}" if team_session.current_innings == 2 else ""

            resume_text = (
                f"{emojis.REFRESH} <b>TEAM MATCH SUCCESSFULLY RESTORED!</b> {emojis.BAT}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📍 <b>Match {team_session.match_id} (Inn {team_session.current_innings})</b>\n"
                f"📊 <b>Score:</b> {bat_name} {curr_tot}/{curr_w} ({ov_s}/{team_session.max_overs} ov){tgt_s}\n"
                f"🏏 <b>Striker:</b> {striker.name if striker else 'None'}\n"
                f"🏃 <b>Non-Striker:</b> {non_striker.name if non_striker else 'None'}\n"
                f"🥎 <b>Bowler:</b> {bowler.name if bowler else 'None'}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
            )
            if striker and bowler:
                await message.reply_html(resume_text)
                await _start_delivery(context, team_session)
            else:
                bat_cap_name = team_session.get_batting_captain_name()
                bowl_cap_name = team_session.get_bowling_captain_name()
                prompt_lines = []
                if not striker:
                    prompt_lines.append(f"👉 Batting Captain <b>{bat_cap_name}</b> (or Host), choose batter: <code>/batting</code>")
                if not bowler:
                    prompt_lines.append(f"👉 Bowling Captain <b>{bowl_cap_name}</b> (or Host), choose bowler: <code>/bowling</code>")
                resume_text += "\n" + "\n".join(prompt_lines)
                await message.reply_html(resume_text)
            return

        match_id = data.get("match_id", f"m{int(time.time())}")
        host_id = data.get("host_id")
        host_name = data.get("host_name", "Host")
        balls_per_turn = data.get("balls_per_turn", 6)

        session = MatchSession(
            match_id=match_id,
            chat_id=chat_id,
            host_id=host_id,
            host_name=host_name,
            mode="solo",
            status="active",
            balls_per_turn=balls_per_turn,
            countdown_seconds=0,
            unlimited_overs=True
        )
        if not session:
            await query.answer(
                "A match is already in progress in this group. Finish or cancel it before starting another.",
                show_alert=True,
            )
            return
        session.celebrated_milestones = {
            tuple(milestone)
            for milestone in data.get("celebrated_milestones", [])
        }
        session.current_batter_idx = data.get("current_batter_idx", 0)
        session.current_bowler_idx = data.get("current_bowler_idx", 1)
        session.current_turn_ball = data.get("current_turn_ball", 0)
        session.sent_media_events = set(data.get("sent_media_events", []))

        players_data = data.get("players", [])
        session.players = []
        for pd in players_data:
            p = PlayerSessionStats(
                user_id=pd["user_id"],
                name=pd["name"],
                runs=pd.get("runs", 0),
                balls=pd.get("balls", 0),
                fours=pd.get("fours", 0),
                sixes=pd.get("sixes", 0),
                wickets=pd.get("wickets", 0),
                runs_conceded=pd.get("runs_conceded", 0),
                balls_bowled=pd.get("balls_bowled", 0),
                is_out=pd.get("is_out", False),
                has_batted=pd.get("has_batted", False),
                is_afk=pd.get("is_afk", False),
            )
            session.players.append(p)

        match_service.active_sessions[match_id] = session

        batter = session.current_batter
        bowler = session.current_bowler
        bot_info = await context.bot.get_me()

        resume_text = (
            f"{emojis.REFRESH} <b>MATCH SUCCESSFULLY RESTORED!</b> {emojis.BAT}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏏 <b>Batter:</b> {mention(batter)} ({batter.runs} runs off {batter.balls}b)\n"
            f"🎯 <b>Bowler:</b> {mention(bowler)} (Ball {session.current_turn_ball + 1}/{session.balls_per_turn})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 {mention(bowler)}, open your DM with @{bot_info.username} to bowl!"
        )
        pulse_kb = get_pulse_keyboard(bot_info.username)
        await message.reply_html(resume_text, reply_markup=pulse_kb)

        try:
            bowler_kb = get_bowling_keyboard(match_id)
            next_ball_num = session.current_turn_ball + 1
            await context.bot.send_message(
                chat_id=bowler.user_id,
                text=(
                    f"🔔 <b>MATCH RESTORED • YOUR TURN TO BOWL!</b>\n"
                    f"{emojis.TARGET} <b>Match {match_id} • Ball {next_ball_num}/{session.balls_per_turn}</b>\n"
                    f"You are bowling to <b>{batter.name}</b>!\n"
                    "Choose your delivery number (1-6):"
                ),
                parse_mode="HTML",
                reply_markup=bowler_kb
            )
        except Exception:
            pass

        start_bowler_timer(context, session)
    except Exception as e:
        await message.reply_html(f"{emojis.CROSS} Failed to restore match: <code>{e}</code>")

async def soloscore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    session = match_service.get_active_session_for_chat(chat.id)
    if not session:
        await update.message.reply_html(f"{emojis.CROSS} No active match in this chat.")
        return

    text = render_scorecard(session, is_final=(session.status == "completed"))
    banner_path = media_service.get_local_media_path("scorecard")
    if banner_path and os.path.exists(banner_path) and len(text) <= 1024 and update.message:
        try:
            with open(banner_path, "rb") as f_photo:
                await update.message.reply_photo(
                    photo=f_photo,
                    caption=text,
                    parse_mode="HTML"
                )
                return
        except Exception:
            pass
    await update.message.reply_html(text)

async def sololineup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = match_service.get_active_session_for_chat(update.effective_chat.id)
    if not session:
        await update.message.reply_html("⚠️ No active Solo match exists in this group.")
        return
    await update.message.reply_html(render_solo_lineup(session))

async def start_match_flow(update: Optional[Update], context: ContextTypes.DEFAULT_TYPE, session: MatchSession):
    started = await match_service.start_match(session.match_id)
    if not started or len(started.players) < 2:
        return

    await _edit_lobby_message(context, started, render_started_text)

    await context.bot.send_message(
        chat_id=started.chat_id,
        text=render_solo_lineup(started),
        parse_mode="HTML",
    )

    batter = started.current_batter
    bowler = started.current_bowler
    if batter:
        await send_batter_entry_media(
            context,
            started,
            f"solo:{batter.user_id}",
            batter.name,
        )
    bot_info = await context.bot.get_me()

    target_chat_id = started.chat_id

    commence_text = (
        f"{emojis.LIGHTNING} <b>MATCH COMMENCED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏏 <b>BATTER:</b> {mention(batter)}\n"
        f"🎯 <b>BOWLER:</b> {mention(bowler)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 {mention(bowler)}, you are <b>BOWLING</b>! Open private DM with @{bot_info.username} to select delivery (1-6).\n"
        f"🏏 {mention(batter)}, you are <b>BATTING</b>! Get ready to hit 1-6 in this chat!"
    )
    pulse_kb = get_pulse_keyboard(bot_info.username)
    pulse_video = media_service.get_local_media_path("pulse")
    await send_event_media(context, target_chat_id, pulse_video, commence_text, reply_markup=pulse_kb, event_key="pulse")

    # Prompt bowler in DM
    try:
        bowler_kb = get_bowling_keyboard(started.match_id)
        await context.bot.send_message(
            chat_id=bowler.user_id,
            text=(
                f"🔔 <b>IT'S YOUR TURN TO BOWL!</b>\n"
                f"{emojis.TARGET} <b>Match {started.match_id} • Ball 1/{started.balls_per_turn}</b>\n"
                f"You are bowling to <b>{batter.name}</b>!\n"
                "Choose your delivery number (1-6):"
            ),
            parse_mode="HTML",
            reply_markup=bowler_kb
        )
    except Exception:
        await context.bot.send_message(
            chat_id=target_chat_id,
            text=f"{emojis.WARNING} {mention(bowler)}, please open DM with @{bot_info.username} to choose your delivery!",
            parse_mode="HTML"
        )

    start_bowler_timer(context, started)

async def handle_private_bowl_deeplink(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str):
    match_id = param.replace("bowl_", "")
    session = match_service.get_session(match_id)
    if not session or session.status != "active":
        await update.message.reply_html(f"{emojis.CROSS} Match is no longer active.")
        return

    bowler = session.current_bowler
    batter = session.current_batter
    if not bowler or update.effective_user.id != bowler.user_id:
        await update.message.reply_html(f"{emojis.CROSS} You are not the active bowler right now!")
        return

    bowler_kb = get_bowling_keyboard(match_id)
    ball_num = session.current_turn_ball + 1
    await update.message.reply_html(
        f"🔔 <b>IT'S YOUR TURN TO BOWL!</b>\n"
        f"{emojis.TARGET} <b>Match {match_id} • Ball {ball_num}/{session.balls_per_turn}</b>\n"
        f"Choose delivery number (1-6) to bowl to <b>{batter.name}</b>:",
        reply_markup=bowler_kb
    )

async def handle_chat_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if text not in ("0", "1", "2", "3", "4", "5", "6"):
        return

    pick = int(text)
    user = update.effective_user
    chat = update.effective_chat
    if not user:
        return

    # 1. Private DM -> Bowler pick (Supports both Solo and Team mode bowlers!)
    if chat.type == "private":
        # Check solo active match first
        active_bowl_match = next(
            (
                s for s in match_service.active_sessions.values()
                if s.status == "active" and s.current_bowler and s.current_bowler.user_id == user.id
            ),
            None
        )
        if active_bowl_match:
            if pick == 0:
                await update.message.reply_html(
                    "⚠️ <b>A bowler must choose a number from 1 to 6.</b> Please try again."
                )
                return
            if active_bowl_match.pending_bowler_pick is not None:
                await update.message.reply_html("🔒 <b>You have already delivered this ball!</b> Waiting for the batter to swing in chat.")
                return
            if should_reject_spamfree_pick(active_bowl_match, user.id, pick):
                await update.message.reply_html("🛡️ Spam-free is ON: the same delivery cannot be used more than twice in a row.")
                return

            cancel_turn_timer(active_bowl_match)
            res = await match_service.record_delivery_interaction(
                match_id=active_bowl_match.match_id,
                user_id=user.id,
                pick=pick,
                role="bowler"
            )
            if res.get("status") == "error":
                await update.message.reply_html(f"⚠️ {html.escape(res['message'])}")
                return
            record_spamfree_pick(active_bowl_match, user.id, pick)
            batter = active_bowl_match.current_batter
            b_name = html.escape(batter.name) if batter else "batter"
            await update.message.reply_html(
                f"🔒 <b>Delivery locked in: {pick}!</b>\n"
                f"Waiting for {b_name} to swing in chat..."
            )

            if batter:
                batting_prompt = (
                    f"🏏 {mention(batter)} to bat · {batter.runs} ({batter.balls}b) SR {batter.strike_rate:.1f}\n"
                    "🎮 <i>Reply 1-6 to play!</i>"
                )
                await send_event_media(
                    context,
                    active_bowl_match.chat_id,
                    media_service.get_local_media_path("batting"),
                    batting_prompt,
                    event_key="batting",
                )
                start_batter_timer(context, active_bowl_match)
            return

        # Check team active match for bowler in DM
        from ..services.team_match_service import team_match_service
        from .team import cancel_team_turn_timer, start_team_batter_timer
        active_team_match = next(
            (
                ts for ts in team_match_service.active_sessions.values()
                if ts.status == "active" and ts.get_current_bowler() and ts.get_current_bowler().user_id == user.id
            ),
            None
        )
        if active_team_match:
            if pick == 0:
                await update.message.reply_html(
                    "⚠️ <b>A bowler must choose a number from 1 to 6.</b> Please try again."
                )
                return
            if active_team_match.pending_bowler_pick is not None:
                await update.message.reply_html("🔒 <b>You have already delivered this ball!</b> Waiting for the batter in group chat.")
                return
            if should_reject_spamfree_pick(active_team_match, user.id, pick):
                await update.message.reply_html("🛡️ Spam-free is ON: the same delivery cannot be used more than twice in a row.")
                return

            active_team_match.pending_bowler_pick = pick
            record_spamfree_pick(active_team_match, user.id, pick)
            cancel_team_turn_timer(active_team_match)
            await update.message.reply_html(
                f"🔒 <b>Delivery locked in: {pick}!</b>\n"
                f"Ball is delivered! Watch group chat for the shot outcome."
            )
            striker = active_team_match.get_current_striker()
            striker_name = mention(striker) if striker else "Batter"
            s_runs = striker.runs if striker else 0
            s_balls = striker.balls if striker else 0
            s_strike_rate = (s_runs * 100 / s_balls) if s_balls else 0.0

            batting_text = (
                f"🏏 {striker_name} to bat · {s_runs}r ({s_balls}b)\n"
                f"⚡ SR {s_strike_rate:.1f}\n"
                f"🎮 Reply <b>1-6</b> in group chat to play your shot!"
            )
            await send_event_media(
                context,
                active_team_match.chat_id,
                media_service.get_local_media_path("batting"),
                batting_text,
                event_key="batting",
            )
            start_team_batter_timer(context, active_team_match)
            return

        # In private DM, if the user is neither a solo bowler nor a team bowler, ignore!
        return

    # 2. Group Chat -> Batter pick
    active_match = next(
        (
            s for s in match_service.active_sessions.values()
            if s.chat_id == chat.id and s.status == "active"
        ),
        None
    )
    if not active_match or not active_match.current_batter:
        return

    # ONLY respond to the active batter! Non-batters in chat are completely ignored.
    if active_match.current_batter.user_id != user.id:
        if (
            pick == 0
            and active_match.current_bowler
            and active_match.current_bowler.user_id == user.id
        ):
            await update.message.reply_html(
                "⚠️ <b>A bowler must choose a number from 1 to 6.</b> "
                "Please select the delivery in private chat."
            )
        return

    if pick == 0:
        await update.message.reply_html(
            "⚠️ <b>A Solo batter must choose a number from 1 to 6.</b> Please try again."
        )
        return

    # Bowler MUST bowl first!
    if active_match.pending_bowler_pick is None:
        bowler_name = html.escape(active_match.current_bowler.name) if active_match.current_bowler else "Bowler"
        await update.message.reply_html(
            f"⏳ <b>{html.escape(user.first_name)}</b>, waiting for {bowler_name} to deliver in private DM first!"
        )
        return

    cancel_turn_timer(active_match)
    res = await match_service.record_delivery_interaction(
        match_id=active_match.match_id,
        user_id=user.id,
        pick=pick,
        role="batter"
    )
    if res.get("status") == "error":
        await update.message.reply_html(f"⚠️ {html.escape(res['message'])}")
        return
    if res.get("status") == "ball_resolved":
        await update_group_after_delivery(
            context,
            active_match.match_id,
            res,
            reply_to_message_id=update.message.message_id,
        )

async def solo_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user

    # Main Menu and Help callbacks
    if data.startswith(("menu_", "help_")):
        from .start import menu_callback_handler
        await menu_callback_handler(update, context)
        return

    # Leaderboard filter callbacks
    if data.startswith("lb_"):
        filter_key = data.replace("lb_", "")
        from .stats import leaderboard_command
        await leaderboard_command(update, context, filter_key=filter_key)
        return

    # Team mode callback
    if data == "select_team_mode":
        from .team import team_lobby_command
        await team_lobby_command(update, context)
        return
    elif data.startswith(("team_", "tbowl_", "setcap_", "tossdec_", "tosscall_", "toss_", "tbat_", "tbowler_")):
        from .team import team_callback_handler
        await team_callback_handler(update, context)
        return

    # Choose solo format -> ask balls per turn
    if data in ("select_solo_mode", "open_solo"):
        from ..services.team_match_service import team_match_service
        team_session = team_match_service.get_session(update.effective_chat.id)
        if team_session and team_session.status in (
            "lobby", "awaiting_overs", "choosing_captains", "awaiting_toss",
            "toss_call", "toss_decision", "active",
        ):
            await query.answer(
                "A match is already in progress in this group. Finish or cancel it before starting another.",
                show_alert=True,
            )
            return
        # Check if match already exists in this chat
        existing = match_service.get_active_session_for_chat(update.effective_chat.id)
        if existing and existing.status == "active":
            await query.answer("A match is already live in this chat! Only 1 game allowed.", show_alert=True)
            return

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚡ 3 Balls / Turn", callback_data=f"set_balls_{user.id}_3"),
                InlineKeyboardButton("🎯 6 Balls / Turn", callback_data=f"set_balls_{user.id}_6")
            ]
        ])
        text = (
            f"🏏 <b>ULTIMATE CRICKET • SOLO SETUP</b>\n"
            f"👑 Host: <b>{user.first_name}</b>\n\n"
            f"Choose delivery format per turn:"
        )
        await safe_edit_or_reply(query, text, kb)
        return

    elif data.startswith("set_balls_"):
        parts = data.split("_")
        host_id = int(parts[2])
        balls = int(parts[3])
        if user.id != host_id and not permission_service.is_owner(user.id):
            await query.answer("Only the host who initiated the game can select balls!", show_alert=True)
            return

        chat_id = update.effective_chat.id
        from ..services.team_match_service import team_match_service
        team_session = team_match_service.get_session(chat_id)
        if team_session and team_session.status in (
            "lobby", "awaiting_overs", "choosing_captains", "awaiting_toss",
            "toss_call", "toss_decision", "active",
        ):
            await query.answer(
                "A match is already in progress in this group. Finish or cancel it before starting another.",
                show_alert=True,
            )
            return
        existing = match_service.get_active_session_for_chat(chat_id)
        if existing:
            if existing.status == "active":
                await query.answer("A match is already live in this chat! Only 1 game allowed.", show_alert=True)
                return
            existing.balls_per_turn = balls
            session = existing
        else:
            session = await match_service.create_solo_lobby(
                chat_id=chat_id,
                host_id=user.id,
                host_name=user.first_name,
                balls_per_turn=balls,
                unlimited_overs=True
            )
            if not session:
                await query.answer(
                    "A match is already in progress in this group. Finish or cancel it before starting another.",
                    show_alert=True,
                )
                return
        is_new_lobby = not getattr(session, "lobby_message_id", None)
        if is_new_lobby and query.message:
            session.lobby_message_id = query.message.message_id
            session.lobby_has_media = bool(
                getattr(query.message, "photo", None)
                or getattr(query.message, "video", None)
                or getattr(query.message, "animation", None)
            )
        text = render_lobby_text(session)
        # Clean text with NO BUTTONS for joinsolo and forcestart
        edited = False
        if getattr(session, "lobby_message_id", None) and query.message and session.lobby_message_id == query.message.message_id:
            edited = await _edit_lobby_message(context, session, render_lobby_text)
        if not edited:
            await safe_edit_or_reply(query, text, reply_markup=None)
            if is_new_lobby:
                session.lobby_message_id = None  # could not edit in place: no live updates
        if is_new_lobby and session.lobby_message_id:
            try:
                await context.bot.pin_chat_message(
                    chat_id=session.chat_id, message_id=session.lobby_message_id, disable_notification=True
                )
                session.lobby_pinned = True
            except Exception as e_pin:
                logger.warning("Could not pin solo lobby message: %s", e_pin)
            try:
                await context.bot.send_message(chat_id=session.chat_id, text="👉 /joinsolo to join")
            except Exception as e_msg:
                logger.warning("Could not send join prompt: %s", e_msg)
        return

    elif data == "close_desk":
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    # Midgame join accept / deny
    if data.startswith("accept_mid_"):
        parts = data.split("_")
        match_id = parts[2]
        target_uid = int(parts[3])
        session = match_service.get_session(match_id)
        if not session:
            await query.answer("Match is no longer active!", show_alert=True)
            return
        is_admin = await permission_service.is_chat_admin(context.bot, session.chat_id, user.id)
        if session.host_id != user.id and not is_admin:
            await query.answer("❌ Only the match host or chat admin can accept join requests!", show_alert=True)
            return

        new_p = await match_service.accept_midgame_join(match_id, target_uid)
        if new_p:
            await safe_edit_or_reply(query, f"✅ <b>{new_p.name}</b> has been accepted by host and added to match rotation!")
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=f"🎉 {mention(new_p)} joined the active match rotation! Total players: <b>{len(session.players)}</b>",
                parse_mode="HTML"
            )
        else:
            await query.answer("Player already added or request expired.", show_alert=True)
        return

    elif data.startswith("deny_mid_"):
        parts = data.split("_")
        match_id = parts[2]
        target_uid = int(parts[3])
        session = match_service.get_session(match_id)
        if not session:
            await query.answer("Match is no longer active!", show_alert=True)
            return
        is_admin = await permission_service.is_chat_admin(context.bot, session.chat_id, user.id)
        if session.host_id != user.id and not is_admin:
            await query.answer("❌ Only the match host or chat admin can deny join requests!", show_alert=True)
            return

        d_name = match_service.deny_midgame_join(match_id, target_uid)
        await safe_edit_or_reply(query, f"❌ Join request for <b>{d_name or 'player'}</b> was declined by the host.")
        return

    # End solo confirmation
    if data.startswith("endsolo_yes_"):
        match_id = data.replace("endsolo_yes_", "")
        session = match_service.get_session(match_id)
        if not session:
            await query.answer("Match not found!", show_alert=True)
            return
        is_admin = await permission_service.is_chat_admin(context.bot, session.chat_id, user.id)
        if session.host_id != user.id and not is_admin:
            await query.answer("Only the host or chat admin can confirm ending the match!", show_alert=True)
            return

        cancel_turn_timer(session)
        await unpin_lobby_message(context, session)
        backup_data = {
            "game": "Ultimate Cricket",
            "match_id": session.match_id,
            "chat_id": session.chat_id,
            "host_id": session.host_id,
            "host_name": session.host_name,
            "balls_per_turn": session.balls_per_turn,
            "current_batter_idx": session.current_batter_idx,
            "current_bowler_idx": session.current_bowler_idx,
            "current_turn_ball": session.current_turn_ball,
            "celebrated_milestones": [
                list(milestone) for milestone in session.celebrated_milestones
            ],
            "sent_media_events": sorted(session.sent_media_events),
            "players": [
                {
                    "user_id": p.user_id,
                    "name": p.name,
                    "runs": p.runs,
                    "balls": p.balls,
                    "fours": p.fours,
                    "sixes": p.sixes,
                    "wickets": p.wickets,
                    "runs_conceded": p.runs_conceded,
                    "balls_bowled": p.balls_bowled,
                    "is_out": p.is_out,
                    "has_batted": p.has_batted,
                    "is_afk": p.is_afk,
                }
                for p in session.players
            ]
        }
        json_bytes = json.dumps(backup_data, indent=2).encode("utf-8")
        session.status = "ended"
        match_service.active_sessions.pop(session.match_id, None)

        try:
            await query.message.delete()
        except Exception:
            pass

        try:
            await context.bot.send_document(
                chat_id=session.chat_id,
                document=io.BytesIO(json_bytes),
                filename=f"ultimate_cricket_match_{session.match_id}_backup.json",
                caption=(
                    f"🛑 <b>Match {session.match_id} ended by host.</b>\n\n"
                    f"💾 <b>Match Backup File:</b>\n"
                    f"Reply to this JSON file with <code>/restore</code> to resume this match anytime!"
                ),
                parse_mode="HTML",
                read_timeout=30,
                write_timeout=30
            )
        except Exception as e_doc:
            logger.warning(f"send_document backup failed: {e_doc}")
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=f"🛑 <b>Match {session.match_id} ended by host.</b>",
                parse_mode="HTML"
            )
        return

    elif data.startswith("endsolo_no_"):
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ <b>Match continues!</b> Play on!",
            parse_mode="HTML"
        )
        return

    # Bowler pick (DM)
    elif data.startswith("bowl_"):
        parts = data.split("_")
        match_id = parts[1]
        pick = int(parts[2])
        if not 1 <= pick <= 6:
            await query.answer("A bowler must choose a number from 1 to 6. Please try again.", show_alert=True)
            return

        session = match_service.get_session(match_id)
        if not session or session.status != "active":
            await query.answer("Match is not active!", show_alert=True)
            return

        if not session.current_bowler or session.current_bowler.user_id != user.id:
            await query.answer("⚠️ Only the active bowler can bowl!", show_alert=True)
            return

        if session.pending_bowler_pick is not None:
            await query.answer("🔒 You have already delivered this ball! Waiting for batter.", show_alert=True)
            return
        if should_reject_spamfree_pick(session, user.id, pick):
            await query.answer(
                "Spam-free is ON: the same delivery cannot be used more than twice in a row.",
                show_alert=True,
            )
            return

        cancel_turn_timer(session)
        res = await match_service.record_delivery_interaction(
            match_id=match_id,
            user_id=user.id,
            pick=pick,
            role="bowler"
        )
        if res.get("status") == "error":
            await query.answer(res["message"], show_alert=True)
            return
        record_spamfree_pick(session, user.id, pick)

        batter = session.current_batter if session else None
        b_name = html.escape(batter.name) if batter else "batter"

        # Secret: Bowler's chosen number shown in their private DM
        await safe_edit_or_reply(
            query,
            f"🔒 <b>Delivery locked in: {pick}!</b>\n"
            f"Waiting for {b_name} to swing in chat..."
        )

        if session and batter:
            batting_prompt = (
                f"🏏 {mention(batter)} to bat · {batter.runs} ({batter.balls}b) SR {batter.strike_rate:.1f}\n"
                "🎮 <i>Reply 1-6 to play!</i>"
            )
            await send_event_media(
                context,
                session.chat_id,
                media_service.get_local_media_path("batting"),
                batting_prompt,
                event_key="batting",
            )
            start_batter_timer(context, session)

    # Batter pick (In Chat Button)
    elif data.startswith("bat_"):
        parts = data.split("_")
        match_id = parts[1]
        pick = int(parts[2])
        if not 1 <= pick <= 6:
            await query.answer("A Solo batter must choose a number from 1 to 6. Please try again.", show_alert=True)
            return

        session = match_service.get_session(match_id)
        if not session or not session.current_batter:
            return

        if session.current_batter.user_id != user.id:
            await query.answer(f"Only active batter ({session.current_batter.name}) can bat!", show_alert=True)
            return

        # Bowler MUST deliver first!
        if session.pending_bowler_pick is None:
            await query.answer("⏳ Waiting for bowler to deliver in private DM first!", show_alert=True)
            return

        cancel_turn_timer(session)
        res = await match_service.record_delivery_interaction(
            match_id=match_id,
            user_id=user.id,
            pick=pick,
            role="batter"
        )
        if res.get("status") == "error":
            await query.answer(res["message"], show_alert=True)
            return

        if res.get("status") == "ball_resolved":
            await update_group_after_delivery(context, match_id, res, query)

async def update_group_after_delivery(
    context: ContextTypes.DEFAULT_TYPE,
    match_id: str,
    res: dict,
    query=None,
    reply_to_message_id: Optional[int] = None,
):
    session: MatchSession = res["session"]
    b_res = res["ball_result"]
    batter = res["batter"]
    bowler = res["bowler"]
    bot_info = await context.bot.get_me()

    cancel_turn_timer(session)

    # Bowler DM resolution message removed per user instruction. Only 'Delivery locked in' is shown in DM.

    # 2. Group Chat card: BOWLER'S NUMBER IS NEVER SHOWN IN GC!
    if b_res.is_wicket:
        event_key = "duck" if batter.runs == 0 else "wicket"
        hindi_commentary = get_hindi_commentary("duck" if batter.runs == 0 else "wicket")
        outcome_card = (
            f"🔥 <b>TIMBERRRR! GONE!</b> ❌\n"
            f"📣 <b>HOWZATTT!</b> (Numbers matched!) {emojis.FIRE}\n"
            f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            f"🚶 {mention(batter)} departs for <b>{batter.runs}</b> ({batter.balls}b)!\n"
            f"🎯 <b>Wicket claimed by {mention(bowler)}!</b>"
        )
    else:
        event_key = {
            0: "dot",
            1: "shot_1",
            2: "double",
            3: "single",
            4: "four",
            5: "shot_5",
            6: "six",
        }.get(b_res.runs, "single")
        hindi_commentary = get_hindi_delivery_commentary(b_res.runs)

        outcome_card = (
            f"🏏 {mention(batter)} hit: <b>{b_res.batter_pick}</b>\n"
            f"🏃 <b>Outcome: {b_res.runs} run(s)!</b> {mention(batter)}: <b>{batter.runs}</b> ({batter.balls}b)"
        )

    # Send video/animation with file_id caching for instant autoplay
    video_path = media_service.get_local_media_path(event_key)
    await send_event_media(
        context,
        session.chat_id,
        video_path,
        outcome_card,
        event_key=event_key,
        reply_to_message_id=reply_to_message_id,
    )
    if not b_res.is_wicket:
        previous_runs = batter.runs - b_res.runs
        await send_run_milestone_celebrations(
            context,
            session.chat_id,
            batter,
            previous_runs,
            1,
            session.celebrated_milestones,
        )
    await context.bot.send_message(
        chat_id=session.chat_id,
        text=hindi_commentary,
    )

    # 3. Match Completion -> Send Final Scoreboard then POTM card with player's profile picture
    if res.get("is_match_complete"):
        await send_match_completion_sequence(context, session)
        return

    # 4. Turn Finished (Batter was OUT or over ended in fixed mode) -> Next Batter
    if res.get("turn_finished"):
        next_b = res["next_batter"]
        next_bw = res["next_bowler"]
        summary_data = res.get("over_summary_data")
        if summary_data:
            summary_text = render_over_summary(summary_data)
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=summary_text,
                parse_mode="HTML"
            )

        turn_header = "🔁 <b>BATTER DISMISSED! NEW BATTER AT CREASE!</b>" if b_res.is_wicket else "🔁 <b>INNINGS COMPLETE! NEXT BATTER AT CREASE!</b>"
        turn_msg = (
            f"{turn_header}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚶 <b>Dismissed Batter:</b> {mention(batter)} finished with {batter.runs} runs ({batter.balls}b).\n"
            f"🏏 <b>Batting:</b> {mention(next_b)} is now at the crease!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=turn_msg,
            parse_mode="HTML"
        )
        if next_b:
            await send_batter_entry_media(
                context,
                session,
                f"solo:{next_b.user_id}",
                next_b.name,
            )

        # Send separate message down stating who's the bowler now
        bowler_msg = (
            f"🎯 <b>BOWLER ON DECK!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Bowling:</b> {mention(next_bw)}\n"
            f"🏏 <b>Batting:</b> {mention(next_b)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 {mention(next_bw)}, open your private DM to bowl!"
        )
        pulse_kb = get_pulse_keyboard(bot_info.username)
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=bowler_msg,
            parse_mode="HTML",
            reply_markup=pulse_kb
        )

        try:
            bowler_kb = get_bowling_keyboard(session.match_id)
            await context.bot.send_message(
                chat_id=next_bw.user_id,
                text=(
                    f"🔔 <b>IT'S YOUR TURN TO BOWL!</b>\n"
                    f"{emojis.TARGET} <b>Match {session.match_id} • Ball 1/{session.balls_per_turn}</b>\n"
                    f"You are bowling to <b>{next_b.name}</b>!\n"
                    "Choose your delivery number (1-6):"
                ),
                parse_mode="HTML",
                reply_markup=bowler_kb
            )
        except Exception:
            pass

        start_bowler_timer(context, session)
        return

    # 5. Over Completed (Batter NOT out, unlimited overs) -> Over Summary & Bowling change
    if res.get("over_completed"):
        next_bw = res["next_bowler"]
        summary_data = res.get("over_summary_data")
        if summary_data:
            summary_text = render_over_summary(summary_data)
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=summary_text,
                parse_mode="HTML"
            )

        bowling_change_msg = (
            f"📣 <b>Over complete!</b> 🚨 <b>Bowling change!</b>\n\n"
            f"🏏 <b>Batting:</b> {mention(batter)}\n"
            f"🎯 <b>Bowling:</b> {mention(next_bw)}\n\n"
            f"👉 {mention(next_bw)}, open your private DM to bowl!"
        )
        pulse_kb = get_pulse_keyboard(bot_info.username)
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=bowling_change_msg,
            parse_mode="HTML",
            reply_markup=pulse_kb
        )

        try:
            bowler_kb = get_bowling_keyboard(session.match_id)
            await context.bot.send_message(
                chat_id=next_bw.user_id,
                text=(
                    f"🔔 <b>IT'S YOUR TURN TO BOWL!</b>\n"
                    f"{emojis.TARGET} <b>Match {session.match_id} • Ball 1/{session.balls_per_turn}</b>\n"
                    f"You are bowling to <b>{batter.name}</b>!\n"
                    "Choose your delivery number (1-6):"
                ),
                parse_mode="HTML",
                reply_markup=bowler_kb
            )
        except Exception:
            pass

        start_bowler_timer(context, session)
        return

    # 6. Mid-Over Delivery: Next ball pulse in GC and prompt in bowler's DM
    next_ball_num = session.current_turn_ball + 1
    next_pulse = (
        f"📍 <b>MATCH PULSE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏏 <b>Batting:</b> {mention(batter)} <b>{batter.runs}*</b> ({batter.balls}b)\n"
        f"🎯 <b>Bowling:</b> {mention(bowler)} • Ball {session.current_turn_ball}/{session.balls_per_turn}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 {mention(bowler)}, choose your delivery in private DM!"
    )
    pulse_kb = get_pulse_keyboard(bot_info.username)
    pulse_video = media_service.get_local_media_path("pulse")
    await send_event_media(context, session.chat_id, pulse_video, next_pulse, reply_markup=pulse_kb, event_key="pulse")

    try:
        bowler_kb = get_bowling_keyboard(session.match_id)
        await context.bot.send_message(
            chat_id=bowler.user_id,
            text=(
                f"🔔 <b>IT'S YOUR TURN TO BOWL!</b>\n"
                f"{emojis.TARGET} <b>Match {session.match_id} • Ball {next_ball_num}/{session.balls_per_turn}</b>\n"
                f"You are bowling to <b>{batter.name}</b>!\n"
                "Choose your delivery number (1-6):"
            ),
            parse_mode="HTML",
            reply_markup=bowler_kb
        )
    except Exception:
        pass

    start_bowler_timer(context, session)

async def send_event_media(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    media_path: Optional[str],
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    event_key: Optional[str] = None,
    reply_to_message_id: Optional[int] = None,
):
    """Sends local media using its Telegram media type and caches successful file IDs."""
    reply_kwargs = (
        {"reply_to_message_id": reply_to_message_id}
        if reply_to_message_id is not None
        else {}
    )
    if event_key:
        cached_file_id = media_cache.get(event_key)
        if cached_file_id and not cached_file_id.startswith(("file://", "http://", "https://", "<", "mock")):
            if event_key not in {"shot_1", "shot_5"}:
                try:
                    await context.bot.send_animation(
                        chat_id=chat_id,
                        animation=cached_file_id,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                        **reply_kwargs,
                    )
                    return
                except Exception as e:
                    logger.warning(f"Cached send_animation failed for {event_key}: {e}")
            try:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=cached_file_id,
                    caption=caption,
                    parse_mode="HTML",
                    supports_streaming=True,
                    reply_markup=reply_markup,
                    **reply_kwargs,
                )
                return
            except Exception as e:
                logger.warning(f"Cached send_video failed for {event_key}: {e}")
                media_cache.set(event_key, None)

    if media_path and os.path.exists(media_path):
        fname = os.path.basename(media_path)
        ext = os.path.splitext(media_path)[1].lower()
        if ext in (".jpg", ".jpeg", ".png"):
            try:
                with open(media_path, "rb") as pf:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=pf,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                        **reply_kwargs,
                    )
                return
            except Exception as e_p:
                logger.warning(f"send_photo failed for {media_path}: {e_p}")

        if event_key in {"shot_1", "shot_5"}:
            try:
                with open(media_path, "rb") as video:
                    msg = await context.bot.send_video(
                        chat_id=chat_id,
                        video=video,
                        filename=fname,
                        caption=caption,
                        parse_mode="HTML",
                        supports_streaming=True,
                        reply_markup=reply_markup,
                        **reply_kwargs,
                        read_timeout=60,
                        write_timeout=60
                    )
                if event_key and msg and msg.video:
                    media_cache.set(event_key, msg.video.file_id)
                return
            except Exception as e_video:
                logger.warning("send_video failed for %s: %s", media_path, e_video)

        try:
            with open(media_path, "rb") as animation:
                msg = await context.bot.send_animation(
                    chat_id=chat_id,
                    animation=animation,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    **reply_kwargs,
                    read_timeout=60,
                    write_timeout=60
                )
            if event_key and msg:
                fid = msg.animation.file_id if msg.animation else (msg.video.file_id if msg.video else None)
                if fid:
                    media_cache.set(event_key, fid)
            return
        except Exception as e_anim:
            logger.warning(f"send_animation failed for {media_path}: {e_anim}")
            try:
                with open(media_path, "rb") as vf:
                    msg = await context.bot.send_video(
                        chat_id=chat_id,
                        video=vf,
                        filename=fname,
                        caption=caption,
                        parse_mode="HTML",
                        supports_streaming=True,
                        reply_markup=reply_markup,
                        **reply_kwargs,
                        read_timeout=60,
                        write_timeout=60
                    )
                if event_key and msg and msg.video:
                    media_cache.set(event_key, msg.video.file_id)
                return
            except Exception as e_vid:
                logger.warning(f"send_video failed for {media_path}: {e_vid}")
                try:
                    with open(media_path, "rb") as document:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=document,
                            filename=fname,
                            caption=caption,
                            parse_mode="HTML",
                            reply_markup=reply_markup,
                            **reply_kwargs,
                            read_timeout=60,
                            write_timeout=60
                        )
                    return
                except Exception as e_doc:
                    logger.error(
                        "send_document fallback failed for %s (%s): %s",
                        event_key,
                        media_path,
                        e_doc,
                    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
        **reply_kwargs,
    )


async def send_batter_entry_media(
    context: ContextTypes.DEFAULT_TYPE,
    session: Any,
    event_id: str,
    batter_name: str,
    batter_id: Optional[int] = None,
):
    event_key = f"crease-entry:{event_id}"
    if event_key in session.sent_media_events:
        return
    session.sent_media_events.add(event_key)
    escaped_name = html.escape(batter_name)
    caption_name = (
        f'<a href="tg://user?id={batter_id}">{escaped_name}</a>'
        if batter_id is not None
        else f"<b>{escaped_name}</b>"
    )
    await send_event_media(
        context,
        session.chat_id,
        media_service.get_local_media_path("crease_entry"),
        f"🏏 {caption_name} takes the crease!",
        event_key="crease_entry",
    )
