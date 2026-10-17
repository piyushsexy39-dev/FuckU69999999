import os
import logging
import asyncio
import random
import io
import json
import html
from types import SimpleNamespace
from typing import Any, Optional, List
from sqlalchemy import func, select
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes
from ..services.team_match_service import team_match_service, TeamMatchSession, TeamPlayer
from ..services.match_service import match_service
from ..services.permissions import permission_service
from ..db.models import User as UserRecord
from ..db.session import async_session_factory
from ..services.commentary import get_hindi_commentary, get_hindi_delivery_commentary
from ..services.media_service import media_service
from ..services.media_cache import media_cache
from .solo import (
    send_event_media,
    send_batter_entry_media,
    send_run_milestone_celebrations,
    should_reject_spamfree_pick,
    record_spamfree_pick,
    reset_spamfree_sequence,
)
from ..utils import emojis

logger = logging.getLogger("gullycric.team")
TEAM_SCORE_POSTER_URL = "https://kommodo.ai/i/8BiSmprYnZzpBscqSiLK"
TEAM_BATTING_POSTER_URL = "https://kommodo.ai/i/2Oa9Tv0MpZE0JMMWhPzV"
TEAM_BOWLING_POSTER_URL = "https://kommodo.ai/i/RwXfldrITOr7iCL2f4fl"


def _team_asset_path(filename: str) -> str:
    search_dirs = [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets", "media"),
        os.getcwd(),
    ]
    for directory in search_dirs:
        curr = directory
        for _ in range(5):
            candidate = os.path.join(curr, filename)
            if os.path.isfile(candidate):
                return candidate
            parent = os.path.dirname(curr)
            if parent == curr:
                break
            curr = parent
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


TEAM_SCORE_POSTER_PATH = _team_asset_path("team_score_poster.jpg")
TEAM_BATTING_POSTER_PATH = _team_asset_path("team_batting_poster.jpg")
TEAM_BOWLING_POSTER_PATH = _team_asset_path("team_bowling_poster.jpg")


def _team_user_mention(user_id: Optional[int], display_name: str) -> str:
    name = html.escape(str(display_name))
    if user_id is None:
        return f"<b>{name}</b>"
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def _real_display_name(user: Any) -> str:
    full_name = getattr(user, "full_name", None)
    if isinstance(full_name, str) and full_name.strip():
        return full_name.strip()
    first_name = getattr(user, "first_name", None)
    return first_name.strip() if isinstance(first_name, str) and first_name.strip() else "Player"


def mention(player: Any) -> str:
    if not player:
        return "Player"
    user_id = getattr(player, "user_id", getattr(player, "id", None))
    display_name = getattr(player, "name", getattr(player, "first_name", "Player"))
    return _team_user_mention(user_id, display_name)


def _team_captain_mention(session: TeamMatchSession, team: str) -> str:
    captain_id = session.captain_a_id if team == "team_a" else session.captain_b_id
    roster = session.team_a_players if team == "team_a" else session.team_b_players
    captain = next((player for player in roster if player.user_id == captain_id), None)
    if captain:
        return mention(captain)
    captain_name = session.team_a_name if team == "team_a" else session.team_b_name
    return _team_user_mention(captain_id, captain_name)

async def safe_edit_or_reply(target, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    """Safely edit message or reply without throwing BadRequest: Message is not modified."""
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
            return
        except Exception:
            pass
    if hasattr(target, "effective_message") and target.effective_message:
        try:
            await target.effective_message.reply_html(text, reply_markup=reply_markup)
            return
        except Exception:
            pass

def batters_ready(session: TeamMatchSession) -> bool:
    """True when the crease is fully set: striker present and, if the team
    needs one, a non-striker too. The bowler may only be chosen after this."""
    striker = session.get_current_striker()
    if not striker:
        return False
    return bool(session.get_current_non_striker()) or not session.needs_non_striker()


def _batters_first_text(session: TeamMatchSession) -> str:
    bat_cap = _team_captain_mention(session, session.batting_team)
    if session.striker_idx is None and session.non_striker_idx is None and session.needs_non_striker():
        need = "the striker and non-striker"
    elif session.needs_non_striker() and session.get_current_striker():
        need = "the non-striker"
    else:
        need = "the next batter"
    return (
        f"👉 Batting Captain {bat_cap} (or Host), select {need} first with <code>/batting</code>.\n"
        f"🎳 The bowler can be chosen only after the batters are at the crease."
    )


def get_team_bowling_keyboard(match_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data=f"tbowl_{match_id}_1"),
            InlineKeyboardButton("2", callback_data=f"tbowl_{match_id}_2"),
            InlineKeyboardButton("3", callback_data=f"tbowl_{match_id}_3"),
        ],
        [
            InlineKeyboardButton("4", callback_data=f"tbowl_{match_id}_4"),
            InlineKeyboardButton("5", callback_data=f"tbowl_{match_id}_5"),
            InlineKeyboardButton("6", callback_data=f"tbowl_{match_id}_6"),
        ]
    ])

def cancel_team_turn_timer(session: TeamMatchSession):
    task = session.turn_timer_task
    if task and not task.done():
        # The AFK timer itself calls this when it ends an innings/match. Cancelling
        # the task that is currently running would abort it before the scoreboard,
        # poster and Player of the Match are sent, so never cancel ourselves.
        try:
            is_self = task is asyncio.current_task()
        except RuntimeError:
            is_self = False
        if not is_self:
            task.cancel()
    session.turn_timer_task = None

def start_team_bowler_timer(context: ContextTypes.DEFAULT_TYPE, session: TeamMatchSession):
    cancel_team_turn_timer(session)
    session.turn_timer_task = asyncio.create_task(run_team_bowler_timer(context, session))

def start_team_batter_timer(context: ContextTypes.DEFAULT_TYPE, session: TeamMatchSession):
    cancel_team_turn_timer(session)
    session.turn_timer_task = asyncio.create_task(run_team_batter_timer(context, session))

async def _send_team_event_media_once(
    context: ContextTypes.DEFAULT_TYPE,
    session: TeamMatchSession,
    event_id: str,
    media_key: str,
    caption: str,
):
    if event_id in session.sent_media_events:
        return
    session.sent_media_events.add(event_id)
    await send_event_media(
        context,
        session.chat_id,
        media_service.get_local_media_path(media_key),
        caption,
        event_key=media_key,
    )


def _render_team_final_scoreboard(session: TeamMatchSession, winner_text: str) -> str:
    balls_per_over = max(1, session.balls_per_over)
    lines = [
        "🏆 <b>Match Summary !!⚡</b>",
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈",
        winner_text,
        "",
        "<b>Full Scorecard:</b>",
    ]

    for side, team_name, roster, runs, wickets, balls in (
        ("🅰️", session.team_a_name, session.team_a_players, session.total_runs_a, session.wickets_a, session.balls_bowled_a),
        ("🅱️", session.team_b_name, session.team_b_players, session.total_runs_b, session.wickets_b, session.balls_bowled_b),
    ):
        run_rate = runs * balls_per_over / balls if balls else 0.0
        lines.extend([
            f"{side} <b>{html.escape(team_name)}</b>: {runs}/{wickets} ({team_name} Scorecard: {runs}/{wickets})",
            f"└ ⏳ {_format_team_overs(balls, balls_per_over)} Ov • RR {run_rate:.2f}",
            "",
        ])
        if roster:
            for player in roster:
                strike_rate = player.runs * 100 / player.balls if player.balls else 0.0
                lines.extend([
                    f"👤 {mention(player)} 🎯",
                    f"├ 🏏 {player.runs}({player.balls}) | SR {strike_rate:.1f}",
                ])
                if player.balls_bowled or player.runs_conceded or player.wickets:
                    economy = (
                        player.runs_conceded * balls_per_over / player.balls_bowled
                        if player.balls_bowled
                        else 0.0
                    )
                    bowling = (
                        f"{player.wickets}W | {player.runs_conceded}R | "
                        f"{_format_team_overs(player.balls_bowled, balls_per_over)}ov | Eco {economy:.1f}"
                    )
                else:
                    bowling = "No bowling data"
                lines.append(f"├ 🥎 {bowling}")
                lines.append("")
        else:
            lines.append("• No players recorded")

    batters = [
        player
        for player in session.team_a_players + session.team_b_players
        if player.balls > 0
    ]
    bowlers = [
        player
        for player in session.team_a_players + session.team_b_players
        if player.balls_bowled > 0
    ]
    lines.extend(["", "⭐ <b>BEST PERFORMANCES</b>"])
    if batters:
        best_batter = max(
            batters,
            key=lambda player: (
                player.runs,
                player.fours,
                player.sixes,
                player.runs / player.balls,
            ),
        )
        strike_rate = best_batter.runs / best_batter.balls * 100
        lines.append(
            f"🏏 Top batter: {mention(best_batter)} — "
            f"{best_batter.runs} runs ({best_batter.balls}b), "
            f"{best_batter.fours} fours, {best_batter.sixes} sixes, SR {strike_rate:.1f}"
        )
    else:
        lines.append("🏏 Best batter: No batting figures recorded")

    if bowlers:
        best_bowler = max(
            bowlers,
            key=lambda player: (
                player.wickets,
                -(player.runs_conceded / player.balls_bowled),
                player.balls_bowled,
            ),
        )
        economy = best_bowler.runs_conceded * balls_per_over / best_bowler.balls_bowled
        overs = _format_team_overs(best_bowler.balls_bowled, balls_per_over)
        lines.append(
            f"🎯 Best bowler: {mention(best_bowler)} — "
            f"{best_bowler.wickets}/{best_bowler.runs_conceded}, {overs} ov, Econ {economy:.2f}"
        )
    else:
        lines.append("🎯 Best bowler: No bowling figures recorded")

    return "\n".join(lines)


def _split_scoreboard_message(text: str, limit: int = 3900) -> List[str]:
    chunks: List[str] = []
    current = ""
    for line in text.splitlines():
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


async def _send_team_poster(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    poster_path: str,
    poster_url: str,
    reply_to_message_id: Optional[int] = None,
):
    chunks = _split_scoreboard_message(text)
    caption = chunks[0] if chunks and len(chunks[0]) <= 1024 else None
    photo_args = {"reply_to_message_id": reply_to_message_id} if reply_to_message_id else {}
    cache_key = f"team_poster_{os.path.basename(poster_path)}" if poster_path else None
    cached_id = media_cache.get(cache_key) if cache_key else None
    if (
        cached_id
        and not cached_id.startswith(("<", "http", "file:"))
        and not getattr(context.bot, "_is_mock", False)
        and not hasattr(getattr(context.bot, "send_photo", None), "assert_called")
    ):
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=cached_id,
                caption=caption,
                parse_mode="HTML" if caption else None,
                **photo_args,
            )
            chunks_to_send = chunks[1:] if caption else chunks
            for chunk in chunks_to_send:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="HTML",
                    reply_to_message_id=reply_to_message_id,
                )
            return
        except Exception:
            if cache_key:
                media_cache.set(cache_key, None)

    try:
        if os.path.isfile(poster_path):
            poster = open(poster_path, "rb")
        else:
            poster = poster_url
        if hasattr(poster, "read"):
            with poster:
                msg = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=poster,
                    caption=caption,
                    parse_mode="HTML" if caption else None,
                    **photo_args,
                )
                if msg and getattr(msg, "photo", None) and cache_key:
                    media_cache.set(cache_key, msg.photo[-1].file_id)
        else:
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=poster,
                caption=caption,
                parse_mode="HTML" if caption else None,
                **photo_args,
            )
            if msg and getattr(msg, "photo", None) and cache_key:
                media_cache.set(cache_key, msg.photo[-1].file_id)
    except Exception as error:
        logger.warning("Could not send Team poster %s: %s", poster_path, error)
        chunks_to_send = chunks
    else:
        chunks_to_send = chunks[1:] if caption else chunks

    for chunk in chunks_to_send:
        await context.bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode="HTML",
            reply_to_message_id=reply_to_message_id,
        )


def _render_team_over_summary(session: TeamMatchSession, summary: dict) -> str:
    batter_totals = {}
    for delivery in summary["deliveries"]:
        batter_id = delivery["batter_id"]
        batter = batter_totals.setdefault(
            batter_id,
            {"name": delivery["batter_name"], "runs": 0, "balls": 0},
        )
        batter["runs"] += delivery["runs"]
        batter["balls"] += 1

    deliveries = " • ".join(str(delivery["symbol"]) for delivery in summary["deliveries"])
    lines = [
        f"🏏 <b>OVER {summary['over_number']} COMPLETE 🔥</b>",
        f"🎳 Bowler: {summary['bowler_mention']}",
        f"📊 <b>{summary['runs']} Runs • {summary['wickets']} Wickets</b>",
        f"🎯 {deliveries}",
        "",
        "🏏 <b>BATTERS</b>",
    ]
    for batter_id, batter in batter_totals.items():
        strike_rate = batter["runs"] * 100 / batter["balls"] if batter["balls"] else 0.0
        lines.append(
            f"🟢 {_team_user_mention(batter_id, batter['name'])} — "
            f"{batter['runs']} ({batter['balls']}) | SR {strike_rate:.2f}"
        )
    lines.extend(["", f"⚡ <b>{summary['runs']} RUNS OFF THE OVER!</b>"])
    return "\n".join(lines)


async def _send_team_over_summary(
    context: ContextTypes.DEFAULT_TYPE,
    session: TeamMatchSession,
    summary: Optional[dict],
):
    if not summary:
        return
    await context.bot.send_message(
        chat_id=session.chat_id,
        text=_render_team_over_summary(session, summary),
        parse_mode="HTML",
    )


async def _send_team_over_followup(
    context: ContextTypes.DEFAULT_TYPE,
    session: TeamMatchSession,
    summary: Optional[dict],
):
    if not summary:
        return
    lines = [f"🏏 <b>OVER {summary['over_number']} COMPLETE!</b>"]
    striker = session.get_current_striker()
    if summary.get("ends_swapped") and striker:
        lines.append(f"🔄 Ends swapped! {mention(striker)} is on strike.")
    captain = _team_captain_mention(session, session.bowling_team)
    if batters_ready(session):
        lines.extend([
            "",
            f"👉 Bowling Captain {captain} (or Host), choose next bowler with:",
            "<code>/bowling &lt;number&gt;</code>!",
        ])
    else:
        lines.extend([
            "",
            _batters_first_text(session),
            f"Then Bowling Captain {captain} (or Host) will choose the next bowler with <code>/bowling</code>.",
        ])
    await context.bot.send_message(
        chat_id=session.chat_id,
        text="\n".join(lines),
        parse_mode="HTML",
    )


def _format_team_overs(balls: int, balls_per_over: int) -> str:
    balls_per_over = max(1, balls_per_over)
    return f"{balls // balls_per_over}.{balls % balls_per_over}"


def _team_totals(session: TeamMatchSession, team: str):
    if team == "team_a":
        return session.total_runs_a, session.wickets_a, session.balls_bowled_a
    return session.total_runs_b, session.wickets_b, session.balls_bowled_b


def _render_team_live_score(session: TeamMatchSession) -> str:
    team_a_runs, team_a_wickets, team_a_balls = _team_totals(session, "team_a")
    team_b_runs, team_b_wickets, team_b_balls = _team_totals(session, "team_b")
    batting_name = session.team_a_name if session.batting_team == "team_a" else session.team_b_name
    batting_roster = session.get_batting_roster()
    striker = session.get_current_striker()
    non_striker = session.get_current_non_striker()
    bowler = session.get_current_bowler()
    runs, wickets, balls = _team_totals(session, session.batting_team)
    remaining_balls = max(0, session.max_overs * session.balls_per_over - balls)
    current_rate = runs * session.balls_per_over / balls if balls else 0.0
    team_a_ov = _format_team_overs(team_a_balls, session.balls_per_over)
    team_b_ov = _format_team_overs(team_b_balls, session.balls_per_over)
    header = "📊 <b>FINAL TEAM SCORE</b>" if session.status == "completed" else "📊 <b>TEAM MATCH · LIVE SCORE</b>"
    lines = [
        header,
        f"🔵 <b>{html.escape(session.team_a_name)}</b>: {team_a_runs}/{team_a_wickets} ({team_a_ov} ov)",
        f"🔴 <b>{html.escape(session.team_b_name)}</b>: {team_b_runs}/{team_b_wickets} ({team_b_ov} ov)",
    ]
    if session.status != "completed":
        lines.append(f"<b>INNINGS {session.current_innings}</b> · {html.escape(batting_name)} batting")
        active_batters = [player for player in (striker, non_striker) if player]
        if active_batters:
            for player in active_batters:
                strike_rate = player.runs * 100 / player.balls if player.balls else 0.0
                lines.append(
                    f"🏏 {mention(player)} — {player.runs} ({player.balls}b) · SR {strike_rate:.1f}"
                )
        else:
            lines.append("🏏 No batter selected")
        if bowler:
            economy = (
                bowler.runs_conceded * session.balls_per_over / bowler.balls_bowled
                if bowler.balls_bowled
                else 0.0
            )
            lines.append(
                f"🥎 {mention(bowler)} — {bowler.wickets}/{bowler.runs_conceded} · "
                f"{_format_team_overs(bowler.balls_bowled, session.balls_per_over)} Ov · Eco {economy:.1f}"
            )
        else:
            lines.append("🥎 Bowler not assigned")

        if session.current_innings == 2 and not session.final_result:
            target = session.target + 1
            runs_required = max(0, target - runs)
            rrr = (runs_required * session.balls_per_over / remaining_balls) if remaining_balls else 0.0
            lines.append(
                f"📈 CRR {current_rate:.2f} · RRR {rrr:.2f} · Target {target} · Overs left "
                f"{_format_team_overs(remaining_balls, session.balls_per_over)}"
            )
            wickets_left = max(0, len(batting_roster) - 1 - wickets)
            lines.append(
                f"🎯 Need {runs_required} runs to win · {wickets_left} wickets in hand · "
                f"{remaining_balls} balls left"
            )
        else:
            lines.append(
                f"📈 CRR {current_rate:.2f} · Overs left "
                f"{_format_team_overs(remaining_balls, session.balls_per_over)}"
            )

    if session.final_result:
        lines.extend(["", session.final_result])
    return "\n".join(lines)


def _render_team_detailed_scorecard(session: TeamMatchSession) -> str:
    runs, wickets, balls = _team_totals(session, session.batting_team)
    batting_name = session.team_a_name if session.batting_team == "team_a" else session.team_b_name
    striker = session.get_current_striker()
    non_striker = session.get_current_non_striker()
    bowler = session.get_current_bowler()
    remaining_balls = max(0, session.max_overs * session.balls_per_over - balls)
    current_rate = runs * session.balls_per_over / balls if balls else 0.0
    clean_bat_name = batting_name.rstrip(" 🔴🔵").strip()
    ov_str = _format_team_overs(balls, session.balls_per_over)
    innings_name = "1st" if session.current_innings == 1 else "2nd"
    lines = [
        f"📊 <b>LIVE TEAM SCORECARD</b> · SCORE - {runs}/{wickets} · {ov_str} Ov",
        f"<b>Innings {session.current_innings}</b> · {html.escape(clean_bat_name)} · {innings_name} Inn ({html.escape(clean_bat_name)}: {runs}/{wickets} · {ov_str} Ov)",
    ]
    if session.current_innings == 2:
        target = session.target + 1
        runs_required = max(0, target - runs)
        wickets_left = max(0, len(session.get_batting_roster()) - 1 - wickets)
        lines.append(f"🎯 <b>Target {target}</b> · Runs required {runs_required} · Balls remaining {remaining_balls}")
        lines.append(f"Need {runs_required} runs to win · {wickets_left} wickets in hand · {remaining_balls} balls left")
    lines.extend([
        "",
        f"📈 <b>INNINGS</b> · CRR {current_rate:.2f} · Overs left {_format_team_overs(remaining_balls, session.balls_per_over)}",
        "",
        "🏏 <b>BATTING</b>",
    ])
    for player in session.get_batting_roster():
        if player.is_out:
            status = "OUT"
        elif player is striker:
            status = "at crease 🏏"
        elif player is non_striker:
            status = "at crease"
        elif player.balls == 0 and player.runs == 0:
            status = "yet to bat"
        else:
            status = "batted"
        strike_rate = player.runs * 100 / player.balls if player.balls else 0.0
        lines.append(
            f"• {mention(player)} — {player.runs} ({player.balls}b) [{status}] · SR {strike_rate:.1f}"
        )
    lines.extend(["", "🥎 <b>BOWLING</b>"])
    for player in session.get_bowling_roster():
        if player.balls_bowled > 0 or player is bowler:
            b_ov = _format_team_overs(player.balls_bowled, session.balls_per_over)
            economy = (
                player.runs_conceded * session.balls_per_over / player.balls_bowled
                if player.balls_bowled
                else 0.0
            )
            lines.append(
                f"• {mention(player)} — {player.wickets}/{player.runs_conceded} · bowled {b_ov} ov · Eco {economy:.2f}"
            )
    return "\n".join(lines).rstrip()


async def _update_team_live_score(context: ContextTypes.DEFAULT_TYPE, session: TeamMatchSession):
    text = _render_team_live_score(session)
    message_id = session.live_score_message_id
    if message_id is None and session.status != "active":
        return
    if message_id is not None:
        if text == session.live_score_text:
            return
        try:
            await context.bot.edit_message_text(
                chat_id=session.chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=None,
            )
            session.live_score_text = text
        except BadRequest as error:
            if "message is not modified" in str(error).lower():
                session.live_score_text = text
                return
            logger.warning("Could not update pinned Team score message %s: %s", message_id, error)
        except TelegramError as error:
            logger.warning("Could not update pinned Team score message %s: %s", message_id, error)
        return

    try:
        message = await context.bot.send_message(
            chat_id=session.chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=None,
        )
    except TelegramError as error:
        logger.warning("Could not create pinned Team score message: %s", error)
        return

    session.live_score_message_id = message.message_id
    session.live_score_text = text
    try:
        await context.bot.pin_chat_message(
            chat_id=session.chat_id,
            message_id=message.message_id,
            disable_notification=True,
        )
    except Exception as error:
        logger.warning("Could not pin Team score message %s: %s", message.message_id, error)


async def _send_team_chase_over_update(context: ContextTypes.DEFAULT_TYPE, session: TeamMatchSession):
    runs, wickets, balls = _team_totals(session, session.batting_team)
    target = session.target + 1
    runs_required = max(0, target - runs)
    balls_remaining = max(0, session.max_overs * session.balls_per_over - balls)
    wickets_remaining = max(0, len(session.get_batting_roster()) - 1 - wickets)
    batting_name = session.team_a_name if session.batting_team == "team_a" else session.team_b_name
    if runs_required == 0:
        winning_instruction = f"<b>{html.escape(batting_name)} have reached the target.</b>"
    else:
        winning_instruction = (
            f"<b>{html.escape(batting_name)} need {runs_required} runs from "
            f"{balls_remaining} balls</b> with {wickets_remaining} wickets remaining."
        )
    try:
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"🏏 <b>OVER COMPLETE · CHASE UPDATE</b>\n"
                f"{html.escape(batting_name)}: <b>{runs}/{wickets}</b> · Target: <b>{target}</b>\n"
                f"Runs required: <b>{runs_required}</b> · Balls remaining: <b>{balls_remaining}</b>\n"
                f"Wickets remaining: <b>{wickets_remaining}</b>\n"
                f"{winning_instruction}"
            ),
            parse_mode="HTML",
        )
    except Exception as error:
        logger.warning("Could not send Team chase over update: %s", error)


async def _send_team_detailed_scorecard(update: Update, context: ContextTypes.DEFAULT_TYPE, session: TeamMatchSession):
    text = _render_team_detailed_scorecard(session)
    reply_photo = getattr(getattr(update, "message", None), "reply_photo", None)
    if reply_photo and (hasattr(reply_photo, "assert_called") or hasattr(reply_photo, "await_count")):
        poster = open(TEAM_SCORE_POSTER_PATH, "rb") if os.path.isfile(TEAM_SCORE_POSTER_PATH) else TEAM_SCORE_POSTER_URL
        if hasattr(poster, "read"):
            with poster:
                await reply_photo(photo=poster, caption=text, parse_mode="HTML")
        else:
            await reply_photo(photo=poster, caption=text, parse_mode="HTML")
        return

    await _send_team_poster(
        context,
        session.chat_id,
        text,
        TEAM_SCORE_POSTER_PATH,
        TEAM_SCORE_POSTER_URL,
        reply_to_message_id=getattr(update.message, "message_id", None) if update and update.message else None,
    )


async def _conclude_team_match(context: ContextTypes.DEFAULT_TYPE, session: TeamMatchSession, winner_text: str):
    session.status = "completed"
    session.final_result = winner_text
    cancel_team_turn_timer(session)
    from ..services.economy import economy_service
    for team, is_winner in (
        (session.team_a_players, session.total_runs_a > session.total_runs_b),
        (session.team_b_players, session.total_runs_b > session.total_runs_a),
    ):
        for player in team:
            await economy_service.record_match_stats(
                user_id=player.user_id,
                is_win=is_winner,
                runs=player.runs,
                wickets=player.wickets,
                fours=player.fours,
                sixes=player.sixes,
                match_id=f"team:{session.match_id}",
                mode="team",
                display_name=player.name,
                ducks=int(player.is_out and player.runs == 0),
                balls_faced=player.balls,
                balls_bowled=player.balls_bowled,
                runs_conceded=player.runs_conceded,
                award_diamonds=False,
            )

    potm = session.calculate_potm()
    summary_text = _render_team_final_scoreboard(session, winner_text)
    await _update_team_live_score(context, session)
    await _send_team_poster(
        context,
        session.chat_id,
        summary_text,
        TEAM_SCORE_POSTER_PATH,
        TEAM_SCORE_POSTER_URL,
    )

    if potm:
        potm_caption = (
            f"🎖️ <b>PLAYER OF THE MATCH</b>\n\n"
            f"{mention(potm)} — <b>{potm.runs}</b> ({potm.balls}b) · <b>{potm.wickets}/{potm.runs_conceded}</b>r (+500 Coins Reward)!\n"
            f"<i>A stellar performance under pressure. 🌟</i>"
        )
        from .solo import send_potm_with_pfp
        await send_potm_with_pfp(context, session.chat_id, potm.user_id, potm_caption)

    team_match_service.remove_session(session.chat_id)

async def _handle_innings_or_match_end(context: ContextTypes.DEFAULT_TYPE, session: TeamMatchSession, reason: str = ""):
    cancel_team_turn_timer(session)
    if session.current_innings == 1:
        session.current_innings = 2
        session.target = session.total_runs_a if session.batting_team == "team_a" else session.total_runs_b
        session.batting_team, session.bowling_team = session.bowling_team, session.batting_team
        session.striker_idx = None
        session.non_striker_idx = None
        session.current_bowler_id = None
        session.pending_bowler_pick = None
        session.ball_in_over = 0
        session.current_over_deliveries.clear()
        session.striker_faced_ball = False

        new_bat_name = session.team_a_name if session.batting_team == "team_a" else session.team_b_name
        new_bowl_name = session.team_b_name if session.batting_team == "team_a" else session.team_a_name
        bat_cap_name = _team_captain_mention(session, session.batting_team)
        bowl_cap_name = _team_captain_mention(session, session.bowling_team)

        break_text = (
            f"🏁 <b>INNINGS 1 CONCLUDED!</b>\n"
            + (f"<i>{reason}</i>\n" if reason else "") +
            f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            f"🎯 <b>Target: {session.target + 1} Runs in {session.max_overs} Overs!</b>\n"
            f"Swapping sides! <b>{new_bat_name}</b> will now Bat.\n\n"
            f"👉 Batting Captain {bat_cap_name} (or Host), choose openers first: <code>/batting</code>\n"
            f"🎳 Then Bowling Captain {bowl_cap_name} (or Host) chooses the opening bowler with <code>/bowling</code>"
        )
        await _update_team_live_score(context, session)
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=break_text,
            parse_mode="HTML"
        )
    else:
        if session.total_runs_a > session.total_runs_b:
            diff = session.total_runs_a - session.total_runs_b
            winner_text = f"🎉 <b>Winner: {session.team_a_name} won by {diff} runs!</b>"
        elif session.total_runs_b > session.total_runs_a:
            diff = session.total_runs_b - session.total_runs_a
            winner_text = f"🎉 <b>Winner: {session.team_b_name} won by {diff} runs!</b>"
        else:
            winner_text = "🤝 <b>Match Tied! What an absolute thriller!</b>"
        await _conclude_team_match(context, session, winner_text)

async def run_team_bowler_timer(context: ContextTypes.DEFAULT_TYPE, session: TeamMatchSession):
    """50-second AFK timer for team bowler with 30s and 20s remaining warnings."""
    bowler = session.get_current_bowler()
    if not bowler:
        return
    try:
        await asyncio.sleep(20)
        if (
            session.status != "active"
            or session.pending_bowler_pick is not None
            or session.get_current_bowler() is not bowler
        ):
            return
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"⏳ <b>30s remaining!</b>\n"
                f"🎯 <b>Bowling:</b> {mention(bowler)}\n"
                f"👉 {mention(bowler)}, please open your private DM and choose your delivery (1-6)!"
            ),
            parse_mode="HTML"
        )

        await asyncio.sleep(10)
        if (
            session.status != "active"
            or session.pending_bowler_pick is not None
            or session.get_current_bowler() is not bowler
        ):
            return
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"⚠️ <b>20s remaining!</b>\n"
                f"🎯 <b>Bowling:</b> {mention(bowler)}\n"
                f"👉 {mention(bowler)}, hurry up and bowl or your turn will time out!"
            ),
            parse_mode="HTML"
        )

        await asyncio.sleep(20)
        if (
            session.status != "active"
            or session.pending_bowler_pick is not None
            or session.get_current_bowler() is not bowler
        ):
            return

        old_bowler = bowler
        session.current_bowler_id = None
        session.pending_bowler_pick = None

        penalty_runs = 6
        if session.batting_team == "team_a":
            session.total_runs_a += penalty_runs
        else:
            session.total_runs_b += penalty_runs
        old_bowler.runs_conceded += penalty_runs

        bat_name = session.team_a_name if session.batting_team == "team_a" else session.team_b_name
        curr_total = session.total_runs_a if session.batting_team == "team_a" else session.total_runs_b
        curr_wkts = session.wickets_a if session.batting_team == "team_a" else session.wickets_b
        bowl_cap_name = _team_captain_mention(session, session.bowling_team)

        if session.current_innings == 2 and curr_total > session.target:
            await _update_team_live_score(context, session)
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=(
                    f"⏱️ <b>TIME OUT!</b> {mention(old_bowler)} was AFK and failed to bowl within 50 seconds!\n\n"
                    f"⚠️ <b>+6 PENALTY RUNS</b> awarded to <b>{bat_name}</b>!\n"
                    f"Target of {session.target + 1} has been achieved!"
                ),
                parse_mode="HTML"
            )
            await _conclude_team_match(context, session, f"🎉 <b>Winner: {bat_name} won by penalty runs!</b>")
            return

        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"⏱️ <b>TIME OUT!</b> {mention(old_bowler)} was AFK and failed to bowl within 50 seconds!\n\n"
                f"⚠️ <b>+6 PENALTY RUNS</b> awarded to <b>{bat_name}</b>!\n"
                f"📊 New Score: <b>{bat_name} {curr_total}/{curr_wkts}</b>\n\n"
                f"👉 Bowling Captain {bowl_cap_name} (or Host), choose a new bowler with <code>/bowling</code>!"
            ),
            parse_mode="HTML"
        )
        await _update_team_live_score(context, session)
    except asyncio.CancelledError:
        pass

async def run_team_batter_timer(context: ContextTypes.DEFAULT_TYPE, session: TeamMatchSession):
    """50-second AFK timer for team striker with 30s and 20s remaining warnings."""
    striker = session.get_current_striker()
    bowler = session.get_current_bowler()
    if not striker:
        return
    try:
        await asyncio.sleep(20)
        if (
            session.status != "active"
            or session.pending_bowler_pick is None
            or session.get_current_striker() is not striker
        ):
            return
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"⏳ <b>30s remaining!</b>\n"
                f"🏏 <b>Batting:</b> {mention(striker)}\n"
                f"👉 {mention(striker)}, play your shot (reply 0-6 in this chat)!"
            ),
            parse_mode="HTML"
        )

        await asyncio.sleep(10)
        if (
            session.status != "active"
            or session.pending_bowler_pick is None
            or session.get_current_striker() is not striker
        ):
            return
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"⚠️ <b>20s remaining!</b>\n"
                f"🏏 <b>Batting:</b> {mention(striker)}\n"
                f"👉 {mention(striker)}, swing your bat now or you will be declared OUT!"
            ),
            parse_mode="HTML"
        )

        await asyncio.sleep(20)
        if (
            session.status != "active"
            or session.pending_bowler_pick is None
            or session.get_current_striker() is not striker
        ):
            return

        if session.get_current_striker() is not striker:
            return
        afk_batter = striker
        event_key = "duck" if afk_batter.runs == 0 else "wicket"
        afk_batter.is_out = True
        afk_batter.balls += 1
        session.pending_bowler_pick = None

        if session.batting_team == "team_a":
            session.wickets_a += 1
            session.balls_bowled_a += 1
        else:
            session.wickets_b += 1
            session.balls_bowled_b += 1

        if bowler:
            bowler.wickets += 1
            bowler.balls_bowled += 1

        session.striker_idx = None
        session.striker_faced_ball = False
        session.ball_in_over += 1

        over_complete = False
        if session.ball_in_over >= session.balls_per_over:
            session.ball_in_over = 0
            over_complete = True
            session.current_bowler_id = None
            session.rotate_strike()

        batting_cap_name = _team_captain_mention(session, session.batting_team)
        bowling_cap_name = _team_captain_mention(session, session.bowling_team)

        total_balls = session.max_overs * session.balls_per_over
        curr_b = session.balls_bowled_a if session.batting_team == "team_a" else session.balls_bowled_b
        if session.current_innings == 2 and over_complete:
            await _update_team_live_score(context, session)
            await _send_team_chase_over_update(context, session)
        if session.is_all_out() or curr_b >= total_balls:
            await _send_team_event_media_once(
                context,
                session,
                f"team-dismissal:{session.current_innings}:{afk_batter.user_id}",
                event_key,
                f"❌ {mention(afk_batter)} is out after timing out (AFK).",
            )
            await _handle_innings_or_match_end(context, session, f"⏱️ {mention(afk_batter)} timed out (AFK) and was declared OUT!")
            if session.status == "active":
                await _update_team_live_score(context, session)
            return

        prompt_lines = [f"👉 Batting Captain {batting_cap_name} (or Host), send next batter with <code>/batting</code>!"]
        if over_complete:
            prompt_lines.append(
                f"🏏 <b>OVER COMPLETE!</b>\n🎳 After the batter is at the crease, Bowling Captain {bowling_cap_name} (or Host) chooses the next bowler with <code>/bowling</code>!"
            )

        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"⏱️ <b>TIME OUT!</b> {mention(afk_batter)} was AFK and took more than 50 seconds to bat!\n"
                f"❌ <b>OUT! Timed Out!</b>\n\n"
                + "\n".join(prompt_lines)
            ),
            parse_mode="HTML"
        )
        await _send_team_event_media_once(
            context,
            session,
            f"team-dismissal:{session.current_innings}:{afk_batter.user_id}",
            event_key,
            f"❌ {mention(afk_batter)} is out after timing out (AFK).",
        )
        await _update_team_live_score(context, session)
    except asyncio.CancelledError:
        pass

def render_lobby_text(session: TeamMatchSession) -> str:
    if not session.host_claimed:
        return (
            "👥 <b>TEAM MODE SELECTED!</b>\n"
            "Click below to claim Host:"
        )

    a_names = ", ".join(mention(player) for player in session.team_a_players) or "None yet"
    b_names = ", ".join(mention(player) for player in session.team_b_players) or "None yet"
    host = _team_user_mention(session.host_id, session.host_name or "Host")

    return (
        f"👑 <b>Host set to {host}!</b>\n\n"
        f"👥 <b>TEAM FORMATION PHASE</b>\n"
        f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"🔵 <b>Team A ({len(session.team_a_players)}/min 1):</b>\n↳ {a_names}\n\n"
        f"🔴 <b>Team B ({len(session.team_b_players)}/min 1):</b>\n↳ {b_names}\n\n"
        f"👉 Click below to join a team!"
    )

def render_lobby_keyboard(chat_id: int, session: TeamMatchSession) -> InlineKeyboardMarkup:
    if not session.host_claimed:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👑 I will be the host", callback_data=f"team_claimhost_{chat_id}")],
            [InlineKeyboardButton("❌ Cancel Lobby", callback_data=f"team_cancel_{chat_id}")]
        ])

    # Host claimed: host button DISAPPEARS! Only join buttons, start match, and cancel.
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔵 Join Team A", callback_data=f"team_join_a_{chat_id}"),
            InlineKeyboardButton("🔴 Join Team B", callback_data=f"team_join_b_{chat_id}")
        ],
        [
            InlineKeyboardButton("⚡ Start Match", callback_data=f"team_start_{chat_id}")
        ],
        [
            InlineKeyboardButton("❌ Cancel Lobby", callback_data=f"team_cancel_{chat_id}")
        ]
    ])

async def team_lobby_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initializes a Team lobby from the Play menu or /create_team."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        msg = update.effective_message

        if chat.type not in ("group", "supergroup"):
            err_msg = "⚠️ Team matches can only be played in group chats! Add me to a group to play."
            if update.callback_query:
                await update.callback_query.answer(err_msg, show_alert=True)
            elif msg:
                await msg.reply_html(err_msg)
            return

        existing = team_match_service.get_session(chat.id)
        if existing and existing.status in ("lobby", "awaiting_overs", "choosing_captains", "awaiting_toss", "toss_call", "toss_decision", "active"):
            text = "⚠️ A team match is already active in this chat! Use <code>/teams</code> or <code>/score</code>."
            if update.callback_query:
                await update.callback_query.answer(text, show_alert=True)
            elif msg:
                await msg.reply_html(text)
            return

        from ..services.match_service import match_service
        if match_service.get_active_session_for_chat(chat.id):
            text = "⚠️ A match is already in progress in this group. Finish or cancel it before starting another."
            if update.callback_query:
                await update.callback_query.answer(text, show_alert=True)
            elif msg:
                await msg.reply_html(text)
            return

        session = team_match_service.create_session(chat.id)
        if not session:
            text = "⚠️ A match is already in progress in this group. Finish or cancel it before starting another."
            if update.callback_query:
                await update.callback_query.answer(text, show_alert=True)
            elif msg:
                await msg.reply_html(text)
            return
        text = render_lobby_text(session)
        kb = render_lobby_keyboard(chat.id, session)

        if update.callback_query:
            await safe_edit_or_reply(update.callback_query, text, kb)
        elif msg:
            await msg.reply_html(text, reply_markup=kb)
    except Exception as e:
        logger.error(f"Error in team_lobby_command: {e}", exc_info=True)

async def team_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    if data.startswith("team_endgame_"):
        chat_id = query.message.chat.id if query.message else None
        session = team_match_service.get_session(chat_id) if chat_id is not None else None
        if not session or data not in (
            f"team_endgame_yes_{session.match_id}",
            f"team_endgame_no_{session.match_id}",
        ):
            await query.answer("This Team match is no longer active.", show_alert=True)
            return
        user = update.effective_user
        is_admin = user and await permission_service.is_chat_admin(context.bot, chat_id, user.id)
        if not user or (user.id != session.host_id and not is_admin):
            await query.answer("Only the host or a group administrator can end the match.", show_alert=True)
            return
        if data.startswith("team_endgame_no_"):
            await query.answer("Match continues.")
            await safe_edit_or_reply(query, "✅ <b>The Team match will continue.</b>")
            return
        actor = "owner" if permission_service.is_owner(user.id) else (
            "group administrator" if is_admin and user.id != session.host_id else "host"
        )
        try:
            backup = json.dumps({"game": "Ultimate Cricket", **session.to_dict()}, indent=2).encode("utf-8")
            await context.bot.send_document(
                chat_id=chat_id,
                document=io.BytesIO(backup),
                filename=f"ultimate_cricket_team_{session.match_id}_backup.json",
                caption=(
                    f"🛑 <b>Team Match {session.match_id} ended by {actor}.</b>\n\n"
                    "💾 Reply to this JSON file with <code>/restore</code> to resume the match."
                ),
                parse_mode="HTML",
                read_timeout=30,
                write_timeout=30,
            )
        except Exception as error:
            logger.exception("Could not save Team match backup")
            await query.answer("Could not save the backup; the match remains active.", show_alert=True)
            return
        cancel_team_turn_timer(session)
        session.status = "cancelled"
        session.final_result = f"🛑 Match ended by the {actor}. No winner declared."
        await _update_team_live_score(context, session)
        team_match_service.remove_session(chat_id)
        try:
            await query.message.delete()
        except Exception:
            logger.debug("Could not remove Team end-match confirmation after backup")
        await query.answer("Match ended and backup saved.")
        return

    if not data.startswith(("tosscall_", "tossdec_", "tbowl_")):
        try:
            await query.answer()
        except TelegramError:
            pass

    user = update.effective_user
    chat = update.effective_chat

    # 1. Bowling delivery in DM
    if data.startswith("tbowl_"):
        rem = data[len("tbowl_"):]
        if "_" in rem:
            match_id, pick_str = rem.rsplit("_", 1)
            pick = int(pick_str)
            if not 1 <= pick <= 6:
                await query.answer("A bowler must choose a number from 1 to 6. Please try again.", show_alert=True)
                return
            session = None
            for s in team_match_service.active_sessions.values():
                if s.match_id == match_id:
                    session = s
                    break
            if not session or session.status != "active":
                try:
                    await query.answer("⚠️ This match is no longer active.", show_alert=True)
                except Exception:
                    pass
                return

            bowler = session.get_current_bowler()
            if not bowler or user.id != bowler.user_id:
                try:
                    await query.answer("⚠️ You are not the active bowler right now!", show_alert=True)
                except Exception:
                    pass
                return

            if session.pending_bowler_pick is not None:
                await query.answer("You have already delivered this ball!", show_alert=True)
                return

            if should_reject_spamfree_pick(session, user.id, pick):
                await query.answer(
                    "Spam-free is ON: the same delivery cannot be used more than twice in a row.",
                    show_alert=True,
                )
                return

            session.pending_bowler_pick = pick
            record_spamfree_pick(session, user.id, pick)
            cancel_team_turn_timer(session)
            try:
                await query.edit_message_text(
                    f"🔒 <b>Delivery locked in: {pick}!</b>\n"
                    f"Ball is delivered! Watch group chat for the shot outcome.",
                    parse_mode="HTML"
                )
            except Exception:
                pass

            striker = session.get_current_striker()
            striker_name = mention(striker) if striker else "Batter"
            s_runs = striker.runs if striker else 0
            s_balls = striker.balls if striker else 0
            s_strike_rate = (s_runs * 100 / s_balls) if s_balls else 0.0

            striker_plain = striker.name if striker else "Batter"
            batting_text = (
                f"🏏 {striker_name} ({striker_plain} to bat · {s_runs}r ({s_balls}b)) "
                f"SR {s_strike_rate:.1f}\n"
                f"🎮 Reply <b>1-6</b> in group chat to play your shot!"
            )
            await send_event_media(
                context,
                session.chat_id,
                media_service.get_local_media_path("batting"),
                batting_text,
                event_key="batting",
            )
            start_team_batter_timer(context, session)
        return

    session = team_match_service.get_session(chat.id)
    if not session:
        try:
            await query.answer("⚠️ No active team match in this chat.", show_alert=True)
        except Exception:
            pass
        return

    try:
        # 2. Claim Host (Host button disappears afterwards)
        if data.startswith("team_claimhost_"):
            if session.host_claimed:
                await query.answer("⚠️ Host role already claimed!", show_alert=True)
                return
            session.host_id = user.id
            session.host_name = _real_display_name(user)
            session.host_claimed = True
            try:
                await query.answer(f"👑 You are now the Game Master Host, {user.first_name}!")
            except Exception:
                pass
            await safe_edit_or_reply(query, render_lobby_text(session), render_lobby_keyboard(chat.id, session))
            return

        # 3. Join Team A
        elif data.startswith("team_join_a_"):
            if user.id in session.banned_user_ids:
                await query.answer("🚫 You are banned from this match!", show_alert=True)
                return
            if any(p.user_id == user.id for p in session.team_a_players):
                await query.answer("ℹ️ You are already in Team A!")
                return
            session.add_player("a", user.id, _real_display_name(user))
            try:
                await query.answer("✅ Joined Team A!")
            except Exception:
                pass
            await safe_edit_or_reply(query, render_lobby_text(session), render_lobby_keyboard(chat.id, session))
            return

        # 4. Join Team B
        elif data.startswith("team_join_b_"):
            if user.id in session.banned_user_ids:
                await query.answer("🚫 You are banned from this match!", show_alert=True)
                return
            if any(p.user_id == user.id for p in session.team_b_players):
                await query.answer("ℹ️ You are already in Team B!")
                return
            session.add_player("b", user.id, _real_display_name(user))
            try:
                await query.answer("✅ Joined Team B!")
            except Exception:
                pass
            await safe_edit_or_reply(query, render_lobby_text(session), render_lobby_keyboard(chat.id, session))
            return

        # 5. Host clicks Start Match -> Separate message asks for overs
        elif data.startswith("team_start_"):
            if user.id != session.host_id and not permission_service.is_owner(user.id):
                await query.answer("⚠️ Only the match host can start the match!", show_alert=True)
                return
            if len(session.team_a_players) < 2 or len(session.team_b_players) < 2:
                await query.answer("⚠️ Each team needs at least 2 players to start!", show_alert=True)
                return

            session.status = "awaiting_overs"
            # Edit lobby message to show confirmed
            await safe_edit_or_reply(
                query,
                f"✅ <b>Team rosters confirmed by Host {_team_user_mention(session.host_id, session.host_name or 'Host')}!</b>",
            )

            # Send BRAND NEW SEPARATE MESSAGE asking how many overs
            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    f"⚙️ <b>MATCH OVERS CONFIGURATION</b>\n"
                    f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                    f"👑 Host {_team_user_mention(session.host_id, session.host_name or 'Host')}, how many overs for this match?\n\n"
                    f"👉 Send in group chat: <code>/setovers &lt;number&gt;</code>\n"
                    f"<i>(Example: <code>/setovers 2</code> or <code>/setovers 5</code>)</i>"
                ),
                parse_mode="HTML"
            )
            return

        # 6. Captain selection callbacks
        elif data.startswith("setcap_a_"):
            if session.status != "choosing_captains":
                await query.answer("Captain selection is no longer open.", show_alert=True)
                return
            parts = data.split("_")
            target_id = int(parts[2])
            target_p = next((p for p in session.team_a_players if p.user_id == target_id), None)
            if not target_p:
                return
            session.captain_a_id = target_id
            await query.answer(f"👑 Team A Captain: {target_p.name}")
            await _check_both_captains_chosen(context, session)
            return

        elif data.startswith("setcap_b_"):
            if session.status != "choosing_captains":
                await query.answer("Captain selection is no longer open.", show_alert=True)
                return
            parts = data.split("_")
            target_id = int(parts[2])
            target_p = next((p for p in session.team_b_players if p.user_id == target_id), None)
            if not target_p:
                return
            session.captain_b_id = target_id
            await query.answer(f"👑 Team B Captain: {target_p.name}")
            await _check_both_captains_chosen(context, session)
            return

        # 7. Toss Decision by Winning Captain
        elif data.startswith("tosscall_"):
            toss_payload = data[len("tosscall_"):]
            match_id, toss_choice = toss_payload.rsplit("_", 1)
            if (
                match_id != session.match_id
                or session.status != "toss_call"
                or session.toss_caller_id != user.id
                or toss_choice not in ("heads", "tails")
                or user.id not in (session.captain_a_id, session.captain_b_id)
                or not any(
                    p.user_id == user.id
                    for p in session.team_a_players + session.team_b_players
                )
            ):
                await query.answer("This coin-toss choice is no longer available to you.", show_alert=True)
                return

            session.toss_call = toss_choice.capitalize()
            session.toss_result = random.choice(("Heads", "Tails"))
            caller_side = (
                "team_a" if session.toss_caller_id == session.captain_a_id else "team_b"
            )
            won_toss = session.toss_call == session.toss_result
            session.toss_winner = caller_side if won_toss else (
                "team_b" if caller_side == "team_a" else "team_a"
            )
            session.status = "toss_decision"
            winner_id = session.captain_a_id if session.toss_winner == "team_a" else session.captain_b_id
            session.toss_winner_captain_id = winner_id
            choice_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🏏 Choose Batting", callback_data=f"tossdec_{session.match_id}_bat"),
                InlineKeyboardButton("🥎 Choose Bowling", callback_data=f"tossdec_{session.match_id}_bowl"),
            ]])
            winner_team = session.team_a_name if session.toss_winner == "team_a" else session.team_b_name
            winner_player = next(
                (
                    p for p in (
                        session.team_a_players if session.toss_winner == "team_a"
                        else session.team_b_players
                    ) if p.user_id == winner_id
                ),
                None,
            )
            if not winner_player:
                session.status = "awaiting_toss"
                session.toss_winner = None
                session.toss_winner_captain_id = None
                await query.answer("The toss cannot continue because a captain is unavailable.", show_alert=True)
                return
            toss_result_text = (
                f"🪙 <b>COIN TOSS RESULT</b>\n"
                f"{html.escape(session.toss_call)} called · {html.escape(session.toss_result)} came up.\n"
                f"🎉 <b>{html.escape(winner_team)}</b> won the toss!\n\n"
                f"👑 Captain {mention(winner_player)}, choose bat or bowl:"
            )
            try:
                await query.edit_message_text(
                    toss_result_text,
                    parse_mode="HTML",
                    reply_markup=choice_kb,
                )
            except TelegramError as error:
                logger.warning("Could not update coin-toss message: %s", error)
                await context.bot.send_message(
                    chat_id=session.chat_id,
                    text=toss_result_text,
                    parse_mode="HTML",
                    reply_markup=choice_kb,
                )
            await query.answer()
            return

        elif data.startswith("tossdec_"):
            toss_payload = data[len("tossdec_"):]
            match_id, choice_token = toss_payload.rsplit("_", 1)
            winning_cap_id = session.toss_winner_captain_id
            if (
                match_id != session.match_id
                or session.status != "toss_decision"
                or choice_token not in ("bat", "bowl")
                or user.id != winning_cap_id
                or winning_cap_id != (
                    session.captain_a_id if session.toss_winner == "team_a" else session.captain_b_id
                )
                or not any(
                    player.user_id == winning_cap_id
                    for player in (
                        session.team_a_players if session.toss_winner == "team_a"
                        else session.team_b_players
                    )
                )
            ):
                await query.answer("⚠️ Only the toss-winning captain can choose, and only once.", show_alert=True)
                return

            choice = "Batting" if choice_token == "bat" else "Bowling"
            session.toss_decision = choice

            if choice == "Batting":
                session.batting_team = session.toss_winner
                session.bowling_team = "team_b" if session.toss_winner == "team_a" else "team_a"
            else:
                session.batting_team = "team_b" if session.toss_winner == "team_a" else "team_a"
                session.bowling_team = session.toss_winner

            session.status = "active"
            # Crease is initially empty: NO AUTO-SELECT!
            session.striker_idx = None
            session.non_striker_idx = None
            session.current_bowler_id = None
            cancel_team_turn_timer(session)
            session.striker_faced_ball = False
            await _update_team_live_score(context, session)

            winner_name = session.team_a_name if session.toss_winner == "team_a" else session.team_b_name
            bat_name = session.team_a_name if session.batting_team == "team_a" else session.team_b_name
            bowl_name = session.team_b_name if session.batting_team == "team_a" else session.team_a_name

            bat_cap_name = _team_captain_mention(session, session.batting_team)
            bowl_cap_name = _team_captain_mention(session, session.bowling_team)

            decision_text = (
                    f"🪙 <b>{winner_name} won the toss and chose {choice}!</b>\n"
                    f"Starting 1st Innings ({session.max_overs} Overs)...\n"
                    f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                    f"🔵 <b>Batting:</b> {bat_name} (Captain: {bat_cap_name})\n"
                    f"🔴 <b>Bowling:</b> {bowl_name} (Captain: {bowl_cap_name})\n\n"
                    f"👉 Batting Captain {bat_cap_name} (or Host), choose openers first: <code>/batting</code>\n"
                    f"🎳 Then Bowling Captain {bowl_cap_name} (or Host) chooses the opening bowler with <code>/bowling</code>"
                )
            try:
                await query.edit_message_text(
                    decision_text,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramError as error:
                logger.warning("Could not update toss decision message: %s", error)
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except TelegramError as markup_error:
                    logger.warning("Could not disable old toss buttons: %s", markup_error)
                await context.bot.send_message(
                    chat_id=session.chat_id,
                    text=decision_text,
                    parse_mode="HTML",
                )
            await query.answer()
            return

        # 8. Cancel Lobby
        elif data.startswith("team_cancel_"):
            is_admin = await permission_service.is_chat_admin(context.bot, session.chat_id, user.id)
            if user.id != session.host_id and not is_admin:
                await query.answer("⚠️ Only the host can cancel the lobby!", show_alert=True)
                return
            cancel_team_turn_timer(session)
            team_match_service.remove_session(chat.id)
            await safe_edit_or_reply(query, "❌ <b>Team match lobby cancelled.</b>")
            return

    except Exception as e:
        logger.error(f"Error in team_callback_handler: {e}", exc_info=True)

async def _check_both_captains_chosen(context: ContextTypes.DEFAULT_TYPE, session: TeamMatchSession):
    """When both captains are chosen, announce confirmed captains and prompt /toss."""
    if session.captain_a_id and session.captain_b_id:
        session.status = "awaiting_toss"
        cap_a_p = next((p for p in session.team_a_players if p.user_id == session.captain_a_id), None)
        cap_b_p = next((p for p in session.team_b_players if p.user_id == session.captain_b_id), None)
        cap_a_name = mention(cap_a_p) if cap_a_p else _team_user_mention(session.captain_a_id, "Captain A")
        cap_b_name = mention(cap_b_p) if cap_b_p else _team_user_mention(session.captain_b_id, "Captain B")
        host = _team_user_mention(session.host_id, session.host_name or "Host")

        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"👑 <b>CAPTAINS CONFIRMED!</b>\n"
                f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                f"🔵 <b>Team A Captain:</b> {cap_a_name}\n"
                f"🔴 <b>Team B Captain:</b> {cap_b_name}\n\n"
                f"👉 Host {host}, give command <code>/toss</code> to continue!"
            ),
            parse_mode="HTML"
        )

# ----------------- Commands -----------------

async def set_overs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Host command: /setovers <n>."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        session = team_match_service.get_session(chat.id)
        if not session:
            await update.message.reply_html("❌ No active team match lobby in this chat.")
            return

        if user.id != session.host_id and not permission_service.is_owner(user.id):
            await update.message.reply_html("⚠️ Only the match host can set overs!")
            return

        arg_str = context.args[0].strip("()[] ") if context.args else None
        if not arg_str or not arg_str.isdigit():
            await update.message.reply_html("⚠️ Please specify a valid number of overs: <code>/setovers 2</code>")
            return

        overs = int(arg_str)
        if overs < 1 or overs > 25:
            await update.message.reply_html("⚠️ Match overs must be between 1 and 25!")
            return

        session.max_overs = overs
        # Move to choosing captains
        session.status = "choosing_captains"

        # Build captain selection keyboard
        buttons = []
        # Row for Team A
        row_a = [InlineKeyboardButton(f"🔵 {p.name}", callback_data=f"setcap_a_{p.user_id}") for p in session.team_a_players]
        if row_a:
            buttons.append(row_a[:3])
            if len(row_a) > 3:
                buttons.append(row_a[3:6])
        # Row for Team B
        row_b = [InlineKeyboardButton(f"🔴 {p.name}", callback_data=f"setcap_b_{p.user_id}") for p in session.team_b_players]
        if row_b:
            buttons.append(row_b[:3])
            if len(row_b) > 3:
                buttons.append(row_b[3:6])

        kb = InlineKeyboardMarkup(buttons)

        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"👑 <b>CHOOSE TEAM CAPTAINS</b>\n"
                f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                f"Click below to select the Captain for each team:"
            ),
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in set_overs_command: {e}", exc_info=True)

async def toss_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Host command: /toss."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        session = team_match_service.get_session(chat.id)
        if not session or session.status != "awaiting_toss":
            await update.message.reply_html("❌ Toss is not available right now!")
            return

        if user.id != session.host_id and not permission_service.is_owner(user.id):
            await update.message.reply_html("⚠️ Only the match host can conduct the coin toss!")
            return

        eligible_captains = [
            (captain_id, player)
            for captain_id, roster in (
                (session.captain_a_id, session.team_a_players),
                (session.captain_b_id, session.team_b_players),
            )
            for player in roster
            if captain_id is not None and player.user_id == captain_id
        ]
        if len(eligible_captains) != 2:
            await update.message.reply_html("⚠️ Both team captains must be available before the toss.")
            return

        caller_id, caller = random.choice(eligible_captains)
        session.toss_caller_id = caller_id
        session.toss_call = None
        session.toss_result = None
        session.toss_winner = None
        session.toss_winner_captain_id = None
        session.toss_decision = None
        session.status = "toss_call"
        await _send_team_event_media_once(
            context,
            session,
            "team-toss",
            "toss",
            f"🪙 <b>The toss is about to begin!</b>",
        )
        choice_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Heads", callback_data=f"tosscall_{session.match_id}_heads"),
            InlineKeyboardButton("Tails", callback_data=f"tosscall_{session.match_id}_tails"),
        ]])

        await update.message.reply_html(
            f"🪙 <b>COIN TOSS</b>\n"
            f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            f"👑 Captain {mention(caller)}, choose Heads or Tails:",
            reply_markup=choice_kb,
        )
    except Exception as e:
        logger.error(f"Error in toss_command: {e}", exc_info=True)

async def batting_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Batting Captain or Host selects striker and non-striker: /batting or /batting <n>."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        session = team_match_service.get_session(chat.id)
        if not session or session.status != "active":
            await update.message.reply_html("⚠️ No active team match currently!")
            return

        cap_id = session.get_batting_captain_id()
        if user.id != cap_id and user.id != session.host_id and not permission_service.is_owner(user.id):
            await update.message.reply_html("⚠️ Only the batting captain or host can select batters!")
            return

        roster = session.get_batting_roster()
        arg_str = context.args[0].strip("()[] ") if context.args else None
        team_needs_non_striker = session.needs_non_striker()

        if not arg_str or not arg_str.isdigit():
            team_label = "🅰️" if session.batting_team == "team_a" else "🅱️"
            team_name = session.team_a_name if session.batting_team == "team_a" else session.team_b_name
            striker = session.get_current_striker()
            non_striker = session.get_current_non_striker()
            crease_ids = {
                player.user_id
                for player in (striker, non_striker)
                if player and not player.is_out
            }
            out_count = sum(player.is_out for player in roster)
            to_come = max(0, len(roster) - len(crease_ids) - out_count)
            lines = [
                f"{team_label} <b>{html.escape(team_name)}</b>",
                f"└ 🧢 Cap: {_team_captain_mention(session, session.batting_team)}",
                "",
            ]
            for i, p in enumerate(roster):
                if p.is_out:
                    status = "🚫 OUT"
                elif session.striker_idx == i:
                    status = "🏏 STRIKER"
                elif session.non_striker_idx == i:
                    status = "🏃 NON-STRIKER"
                else:
                    status = "🟢 TO BAT"
                lines.append(f"{i+1}. {mention(p)} {status}")

            lines.extend([
                "",
                f"📊 At crease: {len(crease_ids)} · Out: {out_count} · To come: {to_come}",
            ])

            instruction = ""
            if not team_needs_non_striker:
                if session.striker_idx is None:
                    instruction = "👉 Reply with <code>/batting &lt;number&gt;</code> to send your batter to the crease!"
                else:
                    instruction = "ℹ️ Batter is already at the crease."
            else:
                if session.striker_idx is None and session.non_striker_idx is None:
                    instruction = "👉 Reply with <code>/batting &lt;number&gt;</code> to select your <b>Opening Striker</b>!"
                elif session.striker_idx is not None and session.non_striker_idx is None:
                    instruction = "👉 Reply with <code>/batting &lt;number&gt;</code> to select your <b>Non-Striker</b>!"
                elif session.striker_idx is None and session.non_striker_idx is not None:
                    instruction = "👉 Reply with <code>/batting &lt;number&gt;</code> to select your next <b>Striker</b>!"
                else:
                    instruction = "ℹ️ Both striker and non-striker are already at the crease."

            await _send_team_poster(
                context,
                session.chat_id,
                "\n".join(lines) + f"\n\n{instruction}",
                TEAM_BATTING_POSTER_PATH,
                TEAM_BATTING_POSTER_URL,
                reply_to_message_id=getattr(update.message, "message_id", None) if update and update.message else None,
            )
            return

        idx = int(arg_str) - 1
        if 0 <= idx < len(roster):
            target_p = roster[idx]
            if session.striker_faced_ball and session.striker_idx is None:
                await update.message.reply_html("⚠️ The active batter is locked for this turn after facing a ball.")
                return
            if session.striker_faced_ball and session.striker_idx is not None and session.non_striker_idx is not None:
                await update.message.reply_html("⚠️ The active batter is locked for this turn after facing a ball.")
                return
            bowler = session.get_current_bowler()
            if bowler and target_p.user_id == bowler.user_id:
                await update.message.reply_html("⚠️ The active bowler cannot also bat at the crease.")
                return
            if target_p.is_out:
                await update.message.reply_html("⚠️ That player is already OUT!")
                return
            if session.striker_idx == idx or session.non_striker_idx == idx:
                await update.message.reply_html("ℹ️ That player is already at the crease!")
                return

            active_remaining = [p for p in roster if not p.is_out]

            if not team_needs_non_striker:
                # Solo player in team: only striker needed, no non-striker
                session.striker_idx = idx
                session.non_striker_idx = None
                await send_batter_entry_media(
                    context,
                    session,
                    f"team:{session.current_innings}:{target_p.user_id}",
                    target_p.name,
                    target_p.user_id,
                )
                await update.message.reply_html(
                    f"🏏 {mention(target_p)} has arrived at the crease on strike!"
                )
            else:
                if session.striker_idx is None:
                    session.striker_idx = idx
                    await send_batter_entry_media(
                        context,
                        session,
                        f"team:{session.current_innings}:{target_p.user_id}",
                        target_p.name,
                        target_p.user_id,
                    )
                    if session.non_striker_idx is None and len(active_remaining) >= 2:
                        await _update_team_live_score(context, session)
                        await update.message.reply_html(
                            f"🏏 {mention(target_p)} selected as <b>STRIKER</b>!\n\n"
                            f"👉 Now choose your <b>Non-Striker</b> with <code>/batting &lt;number&gt;</code>!"
                        )
                        return
                    else:
                        await update.message.reply_html(
                            f"🏏 {mention(target_p)} has arrived at the crease on strike!"
                        )
                elif session.non_striker_idx is None:
                    session.non_striker_idx = idx
                    await send_batter_entry_media(
                        context,
                        session,
                        f"team:{session.current_innings}:{target_p.user_id}",
                        target_p.name,
                        target_p.user_id,
                    )
                    await update.message.reply_html(
                        f"🏃 {mention(target_p)} has joined at the non-striker's end!"
                    )
                else:
                    await update.message.reply_html("ℹ️ Both batting positions are already filled!")
                    return

            await _update_team_live_score(context, session)
            striker = session.get_current_striker()
            non_striker = session.get_current_non_striker()

            if striker and (non_striker or not team_needs_non_striker):
                if non_striker:
                    openers_text = (
                        f"🏟️ <b>BATTING DUO AT THE CREASE!</b>\n"
                        f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                        f"🏏 <b>Striker:</b> {mention(striker)}\n"
                        f"🏃 <b>Non-Striker:</b> {mention(non_striker)}\n\n"
                        f"Both batsmen are ready in the middle!"
                    )
                else:
                    openers_text = (
                        f"🏟️ <b>BATTER AT THE CREASE!</b>\n"
                        f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                        f"🏏 <b>Striker:</b> {mention(striker)}\n\n"
                        f"Batter is ready in the middle!"
                    )
                await context.bot.send_message(chat_id=session.chat_id, text=openers_text, parse_mode="HTML")

                # Check if bowler is also ready
                bowler = session.get_current_bowler()
                if bowler:
                    await _start_delivery(context, session)
                else:
                    bowl_cap_name = _team_captain_mention(session, session.bowling_team)
                    await update.message.reply_html(
                        f"⏳ Waiting for Bowling Captain {bowl_cap_name} (or Host) to select bowler with <code>/bowling</code>!"
                    )
        else:
            await update.message.reply_html("⚠️ Invalid batter number!")
    except Exception as e:
        logger.error(f"Error in batting_command: {e}", exc_info=True)

async def bowling_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bowling Captain or Host selects bowler: /bowling or /bowling <n>."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        session = team_match_service.get_session(chat.id)
        if not session or session.status != "active":
            await update.message.reply_html("⚠️ No active team match currently!")
            return

        cap_id = session.get_bowling_captain_id()
        if user.id != cap_id and user.id != session.host_id and not permission_service.is_owner(user.id):
            await update.message.reply_html("⚠️ Only the bowling captain or host can select bowlers!")
            return

        if not batters_ready(session):
            await update.message.reply_html(
                "⚠️ <b>Select the batters first!</b>\n" + _batters_first_text(session)
            )
            return

        roster = session.get_bowling_roster()
        arg_str = context.args[0].strip("()[] ") if context.args else None
        striker = session.get_current_striker()
        non_striker = session.get_current_non_striker()
        eligible_roster = [
            player
            for player in roster
            if player.user_id not in {
                striker.user_id if striker else None,
                non_striker.user_id if non_striker else None,
                session.previous_over_bowler_id,
            }
        ]
        if not arg_str or not arg_str.isdigit():
            bowling_team_name = session.team_a_name if session.bowling_team == "team_a" else session.team_b_name
            assigned_bowler = session.get_current_bowler()
            lines = [
                f"{'🅰️' if session.bowling_team == 'team_a' else '🅱️'} <b>{html.escape(bowling_team_name)}</b>",
                f"└ 🧢 Cap: {_team_captain_mention(session, session.bowling_team)}",
                (
                    f"🎳 Current Bowler: {mention(assigned_bowler)}"
                    if assigned_bowler
                    else "🎳 Current Bowler: Not assigned yet"
                ),
                "",
            ]
            for i, p in enumerate(roster):
                ov_str = _format_team_overs(p.balls_bowled, session.balls_per_over)
                is_active = (session.current_bowler_id == p.user_id)
                tag = " 🥎 ACTIVE" if is_active else ""
                lines.append(f"{i+1}. {mention(p)} — {ov_str}{tag}")

            if not eligible_roster:
                lines.append(
                    "⚠️ No eligible bowler is available for this over. The previous over's bowler cannot bowl consecutively."
                )
            else:
                lines.append(
                    "👉 Reply with <code>/bowling &lt;number&gt;</code> to choose the active bowler!"
                )
            await _send_team_poster(
                context,
                session.chat_id,
                "\n".join(lines),
                TEAM_BOWLING_POSTER_PATH,
                TEAM_BOWLING_POSTER_URL,
                reply_to_message_id=getattr(update.message, "message_id", None) if update and update.message else None,
            )
            return

        if session.ball_in_over > 0:
            await update.message.reply_html("⚠️ The current bowler is locked until this over is complete.")
            return

        idx = int(arg_str) - 1
        if 0 <= idx < len(roster):
            target_p = roster[idx]
            if target_p.user_id not in {player.user_id for player in eligible_roster}:
                await update.message.reply_html(
                    "⚠️ Choose a different eligible player. The active batters and previous over's bowler cannot bowl this over."
                )
                return
            session.current_bowler_id = target_p.user_id
            await _update_team_live_score(context, session)
            await update.message.reply_html(
                f"🥎 {mention(target_p)} is taking the ball to bowl this over!"
            )

            striker = session.get_current_striker()
            non_striker = session.get_current_non_striker()
            need_non_striker = session.needs_non_striker()

            if striker and (non_striker or not need_non_striker):
                await _start_delivery(context, session)
            else:
                bat_cap_name = _team_captain_mention(session, session.batting_team)
                await update.message.reply_html(
                    f"⏳ Waiting for Batting Captain {bat_cap_name} (or Host) to select batter with <code>/batting</code>!"
                )
        else:
            await update.message.reply_html("⚠️ Invalid bowler number!")
    except Exception as e:
        logger.error(f"Error in bowling_command: {e}", exc_info=True)

async def _start_delivery(context: ContextTypes.DEFAULT_TYPE, session: TeamMatchSession):
    """Initiates delivery once striker and bowler are chosen."""
    striker = session.get_current_striker()
    bowler = session.get_current_bowler()
    if not striker or not bowler:
        return
    if striker.user_id == bowler.user_id:
        session.current_bowler_id = None
        await context.bot.send_message(
            chat_id=session.chat_id,
            text="⚠️ The active batter cannot bowl. Please select another eligible bowler with /bowling.",
        )
        return

    await _update_team_live_score(context, session)
    session.pending_bowler_pick = None
    bot_info = await context.bot.get_me()
    dm_url = f"https://t.me/{bot_info.username}"
    dm_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🥎 Open bowling DM", url=dm_url)]
    ])

    non_striker = session.get_current_non_striker()
    non_str_line = f"↳ 🏃 Non-Striker · {mention(non_striker)} <code>{non_striker.runs} ({non_striker.balls}b)</code>\n" if non_striker else ""

    bat_name = session.team_a_name if session.batting_team == "team_a" else session.team_b_name
    curr_total = session.total_runs_a if session.batting_team == "team_a" else session.total_runs_b
    curr_wkts = session.wickets_a if session.batting_team == "team_a" else session.wickets_b
    curr_balls = session.balls_bowled_a if session.batting_team == "team_a" else session.balls_bowled_b
    ov_str = f"{curr_balls // session.balls_per_over}.{curr_balls % session.balls_per_over}"
    target_str = f" • Target: {session.target + 1}" if session.current_innings == 2 else ""

    pulse_text = (
        f"📍 <b>MATCH PULSE (Inn {session.current_innings})</b>\n"
        f"↳ 📊 Score: <b>{bat_name} {curr_total}/{curr_wkts}</b> ({ov_str} ov){target_str}\n"
        f"↳ 🏏 Striker · {mention(striker)} <code>{striker.runs} ({striker.balls}b)</code>\n"
        + non_str_line +
        f"↳ 🥎 Bowler · {mention(bowler)} <code>over {session.ball_in_over}/{session.balls_per_over} balls</code>\n\n"
        f"👉 {mention(bowler)}, check your DM to choose delivery (1-6)!"
    )

    media_path = media_service.get_local_media_path("pulse")
    await send_event_media(context, session.chat_id, media_path, pulse_text, reply_markup=dm_kb, event_key="pulse")

    # Start 50s Bowler AFK Timer
    start_team_bowler_timer(context, session)

    # Send bowling prompt in private DM
    bowler_kb = get_team_bowling_keyboard(session.match_id)
    try:
        await context.bot.send_message(
            chat_id=bowler.user_id,
            text=(
                f"🔔 <b>IT'S YOUR TURN TO BOWL!</b>\n"
                f"🎯 <b>Match {session.match_id} • Ball {session.ball_in_over + 1}/{session.balls_per_over}</b>\n"
                f"You are bowling to {mention(striker)}!\n"
                f"Choose your delivery number (1-6):"
            ),
            parse_mode="HTML",
            reply_markup=bowler_kb
        )
    except Exception as e_dm:
        logger.warning(f"Could not send DM to bowler {bowler.user_id}: {e_dm}")
        try:
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=(
                    f"⚠️ <b>Attention Bowler</b> {mention(bowler)}! Please click <a href='https://t.me/{bot_info.username}'>HERE</a> "
                    f"to start chat with bot in DM to receive your bowling buttons!"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass

async def handle_team_chat_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles 0-6 shot input from the striker in group chat."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        if not update.message or not update.message.text:
            return
        text = update.message.text.strip()
        if text not in ("0", "1", "2", "3", "4", "5", "6"):
            return

        session = team_match_service.get_session(chat.id)
        if not session or session.status != "active":
            return

        striker = session.get_current_striker()
        if not striker or user.id != striker.user_id:
            bowler = session.get_current_bowler()
            if text == "0" and bowler and bowler.user_id == user.id:
                await update.message.reply_html(
                    "⚠️ <b>A bowler must choose a number from 1 to 6.</b> "
                    "Please select again in the bot's private chat."
                )
            return

        bowler = session.get_current_bowler()
        if bowler and bowler.user_id == striker.user_id:
            session.current_bowler_id = None
            await update.message.reply_html(
                "⚠️ The active batter cannot also be the bowler. Please choose an eligible bowler."
            )
            return

        if session.pending_bowler_pick is None:
            await update.message.reply_html(
                f"⏳ {mention(striker)}, waiting for the bowler to deliver in DM first!"
            )
            return
        if not 1 <= session.pending_bowler_pick <= 6:
            session.pending_bowler_pick = None
            await update.message.reply_html(
                "⚠️ <b>A bowler must choose a number from 1 to 6.</b> "
                "The invalid delivery was discarded; please ask the bowler to try again."
            )
            bowler = session.get_current_bowler()
            if bowler:
                await context.bot.send_message(
                    chat_id=bowler.user_id,
                    text="⚠️ A bowler must choose a number from 1 to 6. Please select a delivery again:",
                    reply_markup=get_team_bowling_keyboard(session.match_id),
                )
                start_team_bowler_timer(context, session)
            return

        # Cancel active batter timer
        cancel_team_turn_timer(session)

        batter_pick = int(text)
        bowler_pick = session.pending_bowler_pick
        session.pending_bowler_pick = None
        bowler = session.get_current_bowler()
        delivery_innings = session.current_innings
        striker_runs_before = striker.runs

        is_out = (batter_pick == bowler_pick)
        session.striker_faced_ball = True
        runs_scored = 0

        batting_cap_name = _team_captain_mention(session, session.batting_team)
        bowling_cap_name = _team_captain_mention(session, session.bowling_team)

        if is_out:
            hindi_commentary = get_hindi_commentary("duck" if striker.runs == 0 else "wicket")
            striker_out_name = striker.name
            striker_out_runs = striker.runs
            striker_out_balls = striker.balls
            striker.is_out = True
            striker.balls += 1
            if session.batting_team == "team_a":
                session.wickets_a += 1
            else:
                session.wickets_b += 1

            # Striker leaves crease: NO AUTO-SELECT! Non-striker stays.
            session.striker_idx = None
            session.striker_faced_ball = False
            outcome_title = (
                f"❌ <b>HOWZAT! OUT!</b> (Batter: {batter_pick})\n"
                f"👤 {mention(striker)} departs for <b>{striker_out_runs} runs</b> ({striker_out_balls}b)!"
            )
            event_key = "duck" if striker.runs == 0 else "wicket"
        else:
            runs_scored = batter_pick
            striker.runs += runs_scored
            striker.balls += 1
            hindi_commentary = get_hindi_delivery_commentary(runs_scored)
            if runs_scored == 6:
                striker.sixes += 1
                outcome_title = f"🚀 <b>MAXIMUM! OUT OF THE PARK!</b> (Batter: {batter_pick})"
            elif runs_scored == 4:
                striker.fours += 1
                outcome_title = f"💥 <b>FOUR! CRACKING BOUNDARY!</b> (Batter: {batter_pick})"
            elif runs_scored == 2:
                outcome_title = f"🟢 <b>2 Runs Taken! Great running!</b> (Batter: {batter_pick})"
            elif runs_scored == 1:
                outcome_title = f"🔵 <b>1 Run Taken!</b> (Batter: {batter_pick})"
            elif runs_scored == 0:
                outcome_title = "🟡 <b>DOT BALL! No Run!</b>"
            else:
                outcome_title = f"🏏 <b>{runs_scored} Runs Taken!</b> (Batter: {batter_pick})"
            event_key = {
                0: "dot",
                1: "shot_1",
                2: "double",
                3: "single",
                4: "four",
                5: "shot_5",
                6: "six",
            }.get(runs_scored, "single")

            if session.batting_team == "team_a":
                session.total_runs_a += runs_scored
            else:
                session.total_runs_b += runs_scored

            # STRIKE ROTATION on odd runs (only if both batsmen present):
            if runs_scored in (1, 3, 5):
                session.rotate_strike()

        if bowler:
            bowler.runs_conceded += runs_scored
            bowler.balls_bowled += 1
            if is_out:
                bowler.wickets += 1

        session.current_over_deliveries.append({
            "batter_id": striker.user_id,
            "batter_name": striker.name,
            "runs": runs_scored,
            "is_wicket": is_out,
            "symbol": "W" if is_out else str(runs_scored),
        })

        if session.batting_team == "team_a":
            session.balls_bowled_a += 1
        else:
            session.balls_bowled_b += 1

        session.ball_in_over += 1

        # Check over completion (6 balls)
        over_complete = False
        ends_swapped = False
        if session.ball_in_over >= session.balls_per_over:
            session.ball_in_over = 0
            over_complete = True
            session.previous_over_bowler_id = bowler.user_id if bowler else session.current_bowler_id
            # Over finished: bowler leaves attack -> NO AUTO-SELECT!
            session.current_bowler_id = None
            # Strike rotates at end of over between the two batsmen!
            striker_before_end_swap = session.striker_idx
            non_striker_before_end_swap = session.non_striker_idx
            session.rotate_strike()
            ends_swapped = (
                striker_before_end_swap is not None
                and non_striker_before_end_swap is not None
                and session.striker_idx == non_striker_before_end_swap
                and session.non_striker_idx == striker_before_end_swap
            )

        # Check innings or match finish
        total_balls = session.max_overs * session.balls_per_over
        curr_balls = session.balls_bowled_a if session.batting_team == "team_a" else session.balls_bowled_b
        batting_roster = session.get_batting_roster()
        curr_wickets = session.wickets_a if session.batting_team == "team_a" else session.wickets_b
        over_summary = None
        if over_complete:
            deliveries = list(session.current_over_deliveries)
            over_summary = {
                "over_number": curr_balls // session.balls_per_over,
                "bowler_mention": (
                    _team_user_mention(bowler.user_id, bowler.name)
                    if bowler else "Bowler"
                ),
                "runs": sum(delivery["runs"] for delivery in deliveries),
                "wickets": sum(delivery["is_wicket"] for delivery in deliveries),
                "deliveries": deliveries,
                "ends_swapped": ends_swapped,
            }
            session.current_over_deliveries.clear()

        all_out = session.is_all_out()

        # Check target chased in Innings 2
        match_concluded = False
        winner_text = ""
        if session.current_innings == 2:
            chasing_runs = session.total_runs_b if session.batting_team == "team_b" else session.total_runs_a
            if chasing_runs > session.target:
                match_concluded = True
                winner_team = session.team_b_name if session.batting_team == "team_b" else session.team_a_name
                wickets_in_hand = max(1, len(batting_roster) - 1 - curr_wickets)
                winner_text = f"🎉 <b>Winner: {winner_team} won by {wickets_in_hand} wickets!</b>"

        innings_break = False
        if not match_concluded and (curr_balls >= total_balls or all_out):
            if session.current_innings == 1:
                innings_break = True
                session.current_innings = 2
                session.target = session.total_runs_a if session.batting_team == "team_a" else session.total_runs_b
                session.batting_team, session.bowling_team = session.bowling_team, session.batting_team
                session.striker_idx = None
                session.non_striker_idx = None
                session.striker_faced_ball = False
                session.current_bowler_id = None
                session.pending_bowler_pick = None
                session.ball_in_over = 0
                session.current_over_deliveries.clear()
            else:
                match_concluded = True
                if session.total_runs_a > session.total_runs_b:
                    diff = session.total_runs_a - session.total_runs_b
                    winner_text = f"🎉 <b>Winner: {session.team_a_name} won by {diff} runs!</b>"
                elif session.total_runs_b > session.total_runs_a:
                    diff = session.total_runs_b - session.total_runs_a
                    winner_text = f"🎉 <b>Winner: {session.team_b_name} won by {diff} runs!</b>"
                else:
                    winner_text = "🤝 <b>Match Tied! What an absolute thriller!</b>"

        if match_concluded:
            ball_media = media_service.get_local_media_path(event_key)
            ball_text = outcome_title
            await send_event_media(
                context,
                session.chat_id,
                ball_media,
                ball_text,
                event_key=event_key,
                reply_to_message_id=update.message.message_id,
            )
            if not is_out:
                await send_run_milestone_celebrations(
                    context,
                    session.chat_id,
                    striker,
                    striker_runs_before,
                    delivery_innings,
                    session.celebrated_milestones,
                )
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=hindi_commentary,
            )
            await _send_team_over_summary(context, session, over_summary)
            if over_complete and session.current_innings == 2 and not innings_break:
                await _send_team_chase_over_update(context, session)
            await _conclude_team_match(context, session, winner_text)
            return

        if innings_break:
            ball_media = media_service.get_local_media_path(event_key)
            ball_text = outcome_title
            await send_event_media(
                context,
                session.chat_id,
                ball_media,
                ball_text,
                event_key=event_key,
                reply_to_message_id=update.message.message_id,
            )
            if not is_out:
                await send_run_milestone_celebrations(
                    context,
                    session.chat_id,
                    striker,
                    striker_runs_before,
                    delivery_innings,
                    session.celebrated_milestones,
                )
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=hindi_commentary,
            )
            await _send_team_over_summary(context, session, over_summary)
            await _update_team_live_score(context, session)

            new_bat_name = session.team_a_name if session.batting_team == "team_a" else session.team_b_name
            new_bowl_name = session.team_b_name if session.batting_team == "team_a" else session.team_a_name
            bat_cap_name = _team_captain_mention(session, session.batting_team)
            bowl_cap_name = _team_captain_mention(session, session.bowling_team)
            break_text = (
                f"🏁 <b>INNINGS 1 CONCLUDED!</b>\n"
                f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                f"🎯 <b>Target for {new_bat_name}: {session.target + 1} Runs in {session.max_overs} Overs!</b>\n\n"
                f"🔄 <b>Sides are swapped!</b> {new_bat_name} will now Bat.\n\n"
                f"👉 Batting Captain {bat_cap_name} (or Host), choose openers first with: <code>/batting</code>\n"
                f"🎳 Then Bowling Captain {bowl_cap_name} (or Host) chooses the opening bowler with: <code>/bowling</code>"
            )
            await context.bot.send_message(
                chat_id=session.chat_id,
                text=break_text,
                parse_mode="HTML"
            )
            return

        # Regular delivery outcome announcement
        prompt_lines = []
        if is_out and not all_out:
            non_striker = session.get_current_non_striker()
            ns_info = f" (Non-striker {mention(non_striker)} is waiting at the crease)" if non_striker else ""
            prompt_lines.append(
                f"👉 Batting Captain {batting_cap_name} (or Host), send next batter with: <code>/batting &lt;number&gt;</code>!{ns_info}"
            )
        bat_name = session.team_a_name if session.batting_team == "team_a" else session.team_b_name
        curr_tot = session.total_runs_a if session.batting_team == "team_a" else session.total_runs_b
        curr_w = session.wickets_a if session.batting_team == "team_a" else session.wickets_b
        curr_b = session.balls_bowled_a if session.batting_team == "team_a" else session.balls_bowled_b
        ov_s = f"{curr_b // session.balls_per_over}.{curr_b % session.balls_per_over}"
        tgt_s = f" • Target: {session.target + 1}" if session.current_innings == 2 else ""

        status_text = (
            f"{outcome_title}\n\n"
            f"📍 <b>MATCH SCORE (Inn {session.current_innings})</b>\n"
            f"↳ 📊 {bat_name}: <b>{curr_tot}/{curr_w}</b> ({ov_s} ov){tgt_s}\n"
        )
        if prompt_lines:
            status_text += "\n" + "\n".join(prompt_lines)

        media_path = media_service.get_local_media_path(event_key)
        await send_event_media(
            context,
            session.chat_id,
            media_path,
            status_text,
            event_key=event_key,
            reply_to_message_id=update.message.message_id,
        )
        if not is_out:
            await send_run_milestone_celebrations(
                context,
                session.chat_id,
                striker,
                striker_runs_before,
                delivery_innings,
                session.celebrated_milestones,
            )
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=hindi_commentary,
        )
        await _send_team_over_summary(context, session, over_summary)
        if over_complete:
            await _send_team_over_followup(context, session, over_summary)
        await _update_team_live_score(context, session)
        if over_complete and session.current_innings == 2:
            await _send_team_chase_over_update(context, session)

        # If both batter and bowler are still in position, proceed with next ball
        if session.striker_idx is not None and session.current_bowler_id is not None:
            await _start_delivery(context, session)

    except Exception as e:
        logger.error(f"Error in handle_team_chat_input: {e}", exc_info=True)


# ----------------- Force Add Player (During Match or Anytime) -----------------

async def force_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Host command: /forceadd [a/b] @username or by reply."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        session = team_match_service.get_session(chat.id)
        if not session:
            await update.message.reply_html("❌ No active team match in this chat!")
            return

        if user.id != session.host_id and not permission_service.is_owner(user.id):
            await update.message.reply_html("⚠️ Only the match host can use /forceadd!")
            return

        if not context.args:
            await update.message.reply_html("Usage: <code>/forceadd a @user</code> or <code>/forceadd b @user</code>")
            return

        side = context.args[0].strip("()[] ").lower()
        if side not in ("a", "b", "1", "2"):
            await update.message.reply_html("Usage: <code>/forceadd a @user</code> or <code>/forceadd b @user</code>")
            return

        target_user = None
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
        elif update.message.entities:
            for ent in update.message.entities:
                if ent.type == "text_mention" and ent.user:
                    target_user = ent.user
                    break

        if target_user:
            t_id = target_user.id
            t_name = _real_display_name(target_user)
        elif len(context.args) >= 2:
            raw_arg = context.args[1].lstrip("@")
            async with async_session_factory() as db_session:
                registered_user = (
                    await db_session.execute(
                        select(UserRecord).where(
                            func.lower(UserRecord.username) == raw_arg.lower()
                        )
                    )
                ).scalar_one_or_none()
            if not registered_user:
                await update.message.reply_html(
                    "⚠️ I can't safely resolve that username to a Telegram ID. "
                    "Reply to the player's message or use a Telegram text mention."
                )
                return
            t_id = registered_user.user_id
            t_name = registered_user.first_name or "Player"
        else:
            await update.message.reply_html("⚠️ Please specify a user or reply to their message!")
            return

        team_choice = "a" if side in ("a", "1") else "b"
        session.add_player(team_choice, t_id, t_name)
        if session.status == "active":
            await _update_team_live_score(context, session)
        team_name = session.team_a_name if team_choice == "a" else session.team_b_name

        await update.message.reply_html(
            f"✅ {_team_user_mention(t_id, t_name)} has been forcefully added to "
            f"<b>{html.escape(team_name)}</b> by Host!"
        )
    except Exception as e:
        logger.error(f"Error in force_add_command: {e}", exc_info=True)

async def team_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /team and /teams commands."""
    try:
        chat = update.effective_chat
        session = team_match_service.get_session(chat.id)
        if not session:
            await update.message.reply_html(
                "❌ No active Team match right now! Use the Play menu or <code>/create_team</code> to host one."
            )
            return

        host = _team_user_mention(session.host_id, session.host_name or "Host")
        lines = [
            "🕹️ <b>PLAYING SQUADS</b>",
            f"└ Host: {host}",
            "",
        ]
        for team, name, roster, captain_id in (
            ("team_a", session.team_a_name, session.team_a_players, session.captain_a_id),
            ("team_b", session.team_b_name, session.team_b_players, session.captain_b_id),
        ):
            lines.append(f"<b>{'🅰️' if team == 'team_a' else '🅱️'} {html.escape(name)}</b>")
            if not roster:
                lines.append("└ No players yet")
                lines.append("")
                continue
            for index, player in enumerate(roster, start=1):
                tags = []
                if player.user_id == captain_id:
                    tags.append("🧢")
                if player.is_out:
                    tags.append("🚫")
                elif player.user_id in {
                    p.user_id for p in (
                        session.get_current_striker(),
                        session.get_current_non_striker(),
                    ) if p
                }:
                    tags.append("🏏")
                tag_text = f" {' '.join(tags)}" if tags else ""
                lines.append(f"{index}. {mention(player)}{tag_text}")
            lines.append("")
        await _send_team_poster(
            context,
            session.chat_id,
            "\n".join(lines),
            TEAM_SCORE_POSTER_PATH,
            TEAM_SCORE_POSTER_URL,
            reply_to_message_id=getattr(update.message, "message_id", None) if update and update.message else None,
        )
    except Exception as e:
        logger.error(f"Error in team_command: {e}", exc_info=True)

async def score_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = team_match_service.get_session(update.effective_chat.id)
    if not session or session.status != "active":
        await team_command(update, context)
        return
    await _send_team_detailed_scorecard(update, context, session)

async def change_cap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Team Captain command: /changecap [a/b]."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        session = team_match_service.get_session(chat.id)
        if not session:
            await update.message.reply_html("❌ No active team match setup found!")
            return
        if session.status in ("toss_call", "toss_decision"):
            await update.message.reply_html("⚠️ Captains cannot be changed while the toss is in progress.")
            return

        side = context.args[0].lower() if context.args else ""
        if not side:
            if user.id == session.captain_a_id:
                side = "a"
            elif user.id == session.captain_b_id:
                side = "b"
            else:
                side = "a"

        is_owner = permission_service.is_owner(user.id)
        if side in ("a", "1"):
            if user.id != session.captain_a_id and not is_owner:
                await update.message.reply_html("⚠️ Only Team A's current captain can change Team A's captain!")
                return
            if not session.team_a_players:
                return
            curr_idx = next((i for i, p in enumerate(session.team_a_players) if p.user_id == session.captain_a_id), 0)
            next_idx = (curr_idx + 1) % len(session.team_a_players)
            session.captain_a_id = session.team_a_players[next_idx].user_id
            await update.message.reply_html(f"👑 {mention(session.team_a_players[next_idx])} is now Captain of Team A!")
        elif side in ("b", "2"):
            if user.id != session.captain_b_id and not is_owner:
                await update.message.reply_html("⚠️ Only Team B's current captain can change Team B's captain!")
                return
            if not session.team_b_players:
                return
            curr_idx = next((i for i, p in enumerate(session.team_b_players) if p.user_id == session.captain_b_id), 0)
            next_idx = (curr_idx + 1) % len(session.team_b_players)
            session.captain_b_id = session.team_b_players[next_idx].user_id
            await update.message.reply_html(f"👑 {mention(session.team_b_players[next_idx])} is now Captain of Team B!")
    except Exception as e:
        logger.error(f"Error in change_cap_command: {e}", exc_info=True)

async def shift_player_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Move a player to the other team; only their current team's captain may do this."""
    try:
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        session = team_match_service.get_session(chat.id)
        if not session:
            await message.reply_html("❌ No active team match in this chat.")
            return

        target_user = None
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
        elif context.args:
            raw = context.args[0].strip().lstrip("@")
            if raw.isdigit():
                target_id = int(raw)
                player = next((p for p in session.team_a_players + session.team_b_players if p.user_id == target_id), None)
                if player:
                    target_user = SimpleNamespace(id=player.user_id, first_name=player.name, username=None)
                else:
                    await message.reply_html("⚠️ Player is not in either team.")
                    return
            else:
                async with async_session_factory() as db_session:
                    registered_user = (await db_session.execute(
                        select(UserRecord).where(func.lower(UserRecord.username) == raw.lower())
                    )).scalar_one_or_none()
                if registered_user:
                    target_user = SimpleNamespace(id=registered_user.user_id, first_name=registered_user.first_name or "Player", username=registered_user.username)
                else:
                    await message.reply_html("⚠️ Username not found. Reply to the player's message or use their numeric Telegram ID.")
                    return
        if not target_user:
            await message.reply_html("Usage: reply to a player with <code>/shift</code>, or use <code>/shift @username</code> / <code>/shift user_id</code>.")
            return

        source = "team_a" if any(p.user_id == target_user.id for p in session.team_a_players) else (
            "team_b" if any(p.user_id == target_user.id for p in session.team_b_players) else None
        )
        if not source:
            await message.reply_html("⚠️ That player is not in either team.")
            return
        captain_id = session.captain_a_id if source == "team_a" else session.captain_b_id
        is_host = user.id == session.host_id
        is_own_team_captain = user.id == captain_id
        if not (is_host or is_own_team_captain):
            await message.reply_html("⚠️ Only the host or the captain of that player's current team can use /shift.")
            return

        target = session.get_current_striker()
        non_striker = session.get_current_non_striker()
        bowler = session.get_current_bowler()
        if any(p and p.user_id == target_user.id for p in (target, non_striker, bowler)):
            await message.reply_html("⚠️ A player currently at the crease or bowling cannot be shifted.")
            return

        if source == "team_a":
            player = next(p for p in session.team_a_players if p.user_id == target_user.id)
            session.team_a_players = [p for p in session.team_a_players if p.user_id != target_user.id]
            session.team_b_players.append(player)
            if session.captain_a_id == target_user.id:
                session.captain_a_id = session.team_a_players[0].user_id if session.team_a_players else None
        else:
            player = next(p for p in session.team_b_players if p.user_id == target_user.id)
            session.team_b_players = [p for p in session.team_b_players if p.user_id != target_user.id]
            session.team_a_players.append(player)
            if session.captain_b_id == target_user.id:
                session.captain_b_id = session.team_b_players[0].user_id if session.team_b_players else None

        destination = session.team_b_name if source == "team_a" else session.team_a_name
        await message.reply_html(f"✅ <b>{html.escape(player.name)}</b> has been shifted to <b>{html.escape(destination)}</b>.")
        if session.status == "active":
            await _update_team_live_score(context, session)
    except Exception as e:
        logger.error(f"Error in shift_player_command: {e}", exc_info=True)


async def remove_player_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a player from their team; only that team's captain may do this."""
    try:
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        session = team_match_service.get_session(chat.id)
        if not session:
            await message.reply_html("❌ No active team match in this chat.")
            return

        target_user = None
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
        elif context.args:
            raw = context.args[0].strip().lstrip("@")
            if raw.isdigit():
                target_id = int(raw)
                player = next((p for p in session.team_a_players + session.team_b_players if p.user_id == target_id), None)
                if player:
                    target_user = SimpleNamespace(id=player.user_id, first_name=player.name, username=None)
                else:
                    await message.reply_html("⚠️ Player is not in either team.")
                    return
            else:
                async with async_session_factory() as db_session:
                    registered_user = (await db_session.execute(
                        select(UserRecord).where(func.lower(UserRecord.username) == raw.lower())
                    )).scalar_one_or_none()
                if registered_user:
                    target_user = SimpleNamespace(id=registered_user.user_id, first_name=registered_user.first_name or "Player", username=registered_user.username)
                else:
                    await message.reply_html("⚠️ Username not found. Reply to the player's message or use their numeric Telegram ID.")
                    return
        if not target_user:
            await message.reply_html("Usage: reply to a player with <code>/remove</code>, or use <code>/remove @username</code> / <code>/remove user_id</code>.")
            return

        source = "team_a" if any(p.user_id == target_user.id for p in session.team_a_players) else (
            "team_b" if any(p.user_id == target_user.id for p in session.team_b_players) else None
        )
        if not source:
            await message.reply_html("⚠️ That player is not in either team.")
            return
        captain_id = session.captain_a_id if source == "team_a" else session.captain_b_id
        is_host = user.id == session.host_id
        is_own_team_captain = user.id == captain_id
        if not (is_host or is_own_team_captain):
            await message.reply_html("⚠️ Only the host or that player's team captain can use /remove.")
            return

        striker = session.get_current_striker()
        non_striker = session.get_current_non_striker()
        bowler = session.get_current_bowler()
        if any(p and p.user_id == target_user.id for p in (striker, non_striker)):
            await message.reply_html("⚠️ Cannot remove a batter currently at the crease.")
            return
        if bowler and bowler.user_id == target_user.id:
            await message.reply_html("⚠️ Cannot remove the current active bowler.")
            return

        if source == "team_a":
            player = next(p for p in session.team_a_players if p.user_id == target_user.id)
            session.team_a_players = [p for p in session.team_a_players if p.user_id != target_user.id]
            if session.captain_a_id == target_user.id:
                session.captain_a_id = session.team_a_players[0].user_id if session.team_a_players else None
            team_name = session.team_a_name
        else:
            player = next(p for p in session.team_b_players if p.user_id == target_user.id)
            session.team_b_players = [p for p in session.team_b_players if p.user_id != target_user.id]
            if session.captain_b_id == target_user.id:
                session.captain_b_id = session.team_b_players[0].user_id if session.team_b_players else None
            team_name = session.team_b_name

        await message.reply_html(f"✅ <b>{html.escape(player.name)}</b> has been removed from <b>{html.escape(team_name)}</b>.")
        if session.status == "active":
            await _update_team_live_score(context, session)
    except Exception as e:
        logger.error(f"Error in remove_player_command: {e}", exc_info=True)

async def rebat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Let a dismissed batter bat again: /rebat <number|@username|user_id> or reply to the player.

    Only the host, the bot owner or the batting team's captain may use it. The player
    must belong to the batting team, be OUT, and not be at the crease. Their runs and
    balls are kept and the wicket that was already counted stays on the scorecard; they
    simply become available again in /batting.
    """
    try:
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        session = team_match_service.get_session(chat.id)
        if not session or session.status != "active":
            await message.reply_html("⚠️ No active team match currently!")
            return

        is_allowed = (
            user.id == session.host_id
            or user.id == session.get_batting_captain_id()
            or permission_service.is_owner(user.id)
        )
        if not is_allowed:
            await message.reply_html("⚠️ Only the host, the batting captain or the bot owner can use /rebat.")
            return

        roster = session.get_batting_roster()
        target_idx = None
        if message.reply_to_message and message.reply_to_message.from_user:
            replied_id = message.reply_to_message.from_user.id
            target_idx = next((i for i, p in enumerate(roster) if p.user_id == replied_id), None)
        elif context.args:
            raw = context.args[0].strip("()[] ").lstrip("@")
            if raw.isdigit():
                number = int(raw)
                if 1 <= number <= len(roster):
                    target_idx = number - 1
                else:
                    target_idx = next((i for i, p in enumerate(roster) if p.user_id == number), None)
            else:
                async with async_session_factory() as db_session:
                    registered_user = (await db_session.execute(
                        select(UserRecord).where(func.lower(UserRecord.username) == raw.lower())
                    )).scalar_one_or_none()
                if registered_user:
                    target_idx = next((i for i, p in enumerate(roster) if p.user_id == registered_user.user_id), None)
        else:
            out_players = [f"{i + 1}. {mention(p)}" for i, p in enumerate(roster) if p.is_out]
            lines = ["Usage: <code>/rebat &lt;number&gt;</code>, <code>/rebat @username</code>, or reply to the player with <code>/rebat</code>."]
            lines.append("")
            lines.append("🚫 <b>Out players you can bring back:</b>\n" + "\n".join(out_players) if out_players else "ℹ️ No batter is out right now.")
            await message.reply_html("\n".join(lines))
            return

        if target_idx is None:
            await message.reply_html("⚠️ That player is not in the batting team.")
            return

        player = roster[target_idx]
        if not player.is_out:
            if session.striker_idx == target_idx or session.non_striker_idx == target_idx:
                await message.reply_html("ℹ️ That player is already at the crease!")
            else:
                await message.reply_html("ℹ️ That player is not out, so they can already bat.")
            return

        player.is_out = False
        await message.reply_html(
            f"🔁 {mention(player)} is back in! Runs ({player.runs}) and balls ({player.balls}) are kept.\n"
            f"👉 Batting Captain {_team_captain_mention(session, session.batting_team)} (or Host), "
            "pick batters with <code>/batting</code>."
        )
        await _update_team_live_score(context, session)
    except Exception as e:
        logger.error(f"Error in rebat_command: {e}", exc_info=True)


async def penalty_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Host or bot owner adjusts a team's runs: /penalty +6, /penalty -5.

    Team: reply to a player's message to use that player's team, or add `a` / `b`
    (e.g. `/penalty -5 b`). Otherwise the batting team is used.
    """
    try:
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        session = team_match_service.get_session(chat.id)
        if not session or session.status != "active":
            await message.reply_html("⚠️ No active team match currently!")
            return

        is_allowed = user.id == session.host_id or permission_service.is_owner(user.id)
        if not is_allowed:
            await message.reply_html("⚠️ Only the host or the bot owner can use /penalty.")
            return

        args = list(context.args or [])
        amount = None
        side_arg = None
        for raw in args:
            token = raw.strip()
            if amount is None and token.lstrip("+-").isdigit() and token.lstrip("+-") != "":
                amount = int(token)
            elif token.lower() in ("a", "b", "1", "2", "team_a", "team_b"):
                side_arg = token.lower()
        if amount is None or amount == 0:
            await message.reply_html(
                "Usage: <code>/penalty +6</code> to add runs, <code>/penalty -5</code> to cut runs.\n"
                "Reply to a player to apply it to their team, or add <code>a</code>/<code>b</code> "
                "(e.g. <code>/penalty -5 b</code>). Default: the batting team."
            )
            return
        if abs(amount) > 100:
            await message.reply_html("⚠️ Penalty can be at most 100 runs at a time.")
            return

        team = session.batting_team
        if message.reply_to_message and message.reply_to_message.from_user:
            target_id = message.reply_to_message.from_user.id
            if any(p.user_id == target_id for p in session.team_a_players):
                team = "team_a"
            elif any(p.user_id == target_id for p in session.team_b_players):
                team = "team_b"
        elif side_arg in ("a", "1", "team_a"):
            team = "team_a"
        elif side_arg in ("b", "2", "team_b"):
            team = "team_b"

        if team == "team_a":
            before = session.total_runs_a
            session.total_runs_a = max(0, before + amount)
            applied = session.total_runs_a - before
            team_name = session.team_a_name
            new_total = session.total_runs_a
        else:
            before = session.total_runs_b
            session.total_runs_b = max(0, before + amount)
            applied = session.total_runs_b - before
            team_name = session.team_b_name
            new_total = session.total_runs_b

        # In the 2nd innings the first-batting team's score is the target.
        if session.current_innings == 2 and team != session.batting_team:
            session.target = new_total

        sign = "+" if applied >= 0 else "-"
        icon = "⚠️" if applied >= 0 else "🔻"
        await message.reply_html(
            f"{icon} <b>PENALTY:</b> {sign}{abs(applied)} runs for <b>{html.escape(team_name)}</b>!\n"
            f"📊 New total: <b>{new_total}</b>"
            + (f"\n🎯 Target is now <b>{session.target + 1}</b>." if session.current_innings == 2 and team != session.batting_team else "")
        )

        if session.current_innings == 2 and team == session.batting_team and new_total > session.target:
            batting_roster = session.get_batting_roster()
            curr_wickets = session.wickets_a if team == "team_a" else session.wickets_b
            wickets_in_hand = max(1, len(batting_roster) - 1 - curr_wickets)
            await _update_team_live_score(context, session)
            await _conclude_team_match(
                context, session,
                f"🎉 <b>Winner: {html.escape(team_name)} won by {wickets_in_hand} wickets!</b>",
            )
            return
        await _update_team_live_score(context, session)
    except Exception as e:
        logger.error(f"Error in penalty_command: {e}", exc_info=True)


async def change_host_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Host command: /changehost by replying to user."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        session = team_match_service.get_session(chat.id)
        if not session:
            await update.message.reply_html("❌ No active team match setup found!")
            return
        if user.id != session.host_id and not permission_service.is_owner(user.id):
            await update.message.reply_html("⚠️ Only current host can transfer host role!")
            return
        if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
            await update.message.reply_html("⚠️ Please reply to the user you want to make host with <code>/changehost</code>")
            return
        new_host = update.message.reply_to_message.from_user
        session.host_id = new_host.id
        session.host_name = _real_display_name(new_host)
        await update.message.reply_html(f"👑 Host set to {_team_user_mention(new_host.id, session.host_name)}!")
    except Exception as e:
        logger.error(f"Error in change_host_command: {e}", exc_info=True)

async def spamfree_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat = update.effective_chat
        user = update.effective_user
        team_session = team_match_service.get_session(chat.id)
        session = team_session or match_service.get_active_session_for_chat(chat.id)
        if not session:
            await update.message.reply_html("❌ No active match in this chat.")
            return
        allowed_user_ids = {session.host_id}
        if team_session:
            allowed_user_ids.update((session.captain_a_id, session.captain_b_id))
        if user.id not in allowed_user_ids and not permission_service.is_owner(user.id):
            await update.message.reply_html("⚠️ Only the host, team captains, or bot owner can toggle spam-free mode!")
            return
        session.spamfree = not session.spamfree
        reset_spamfree_sequence(session)
        status = "ENABLED 🟢" if session.spamfree else "DISABLED 🔴"
        await update.message.reply_html(
            f"🛡️ Spam-free mode is now <b>{status}</b>!\n"
            "A bowler cannot use the same delivery more than twice consecutively."
        )
    except Exception as e:
        logger.error(f"Error in spamfree_command: {e}", exc_info=True)

async def end_match_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat = update.effective_chat
        user = update.effective_user
        session = team_match_service.get_session(chat.id)
        if not session:
            await update.message.reply_html("❌ No active team match to end.")
            return
        is_admin = await permission_service.is_chat_admin(context.bot, chat.id, user.id)
        if user.id != session.host_id and not is_admin:
            await update.message.reply_html("⚠️ Only the host or a group administrator can end the match!")
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes, end match", callback_data=f"team_endgame_yes_{session.match_id}"),
            InlineKeyboardButton("❌ No, keep playing", callback_data=f"team_endgame_no_{session.match_id}"),
        ]])
        await update.message.reply_html(
            "⚠️ <b>Are you sure you want to end this Team match?</b>\n"
            "A JSON backup will be sent so the match can be resumed with <code>/restore</code>.",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"Error in end_match_command: {e}", exc_info=True)


async def changeover_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    session = team_match_service.get_session(chat.id) if chat else None
    if not session or session.status != "active":
        await update.message.reply_html("❌ /changeover is available during an active Team match.")
        return
    if user.id != session.host_id and not permission_service.is_owner(user.id):
        await update.message.reply_html("⚠️ Only the match host can change the overs.")
        return
    if session.current_innings != 1:
        await update.message.reply_html("⚠️ Overs can only be changed during the first innings.")
        return

    arg_str = context.args[0].strip("()[] ") if context.args else None
    if not arg_str or not arg_str.isdigit():
        await update.message.reply_html("⚠️ Use <code>/changeover &lt;number&gt;</code> (1-25).")
        return
    overs = int(arg_str)
    if not 1 <= overs <= 25:
        await update.message.reply_html("⚠️ Match overs must be between 1 and 25.")
        return
    balls_already_bowled = (
        session.balls_bowled_a if session.batting_team == "team_a" else session.balls_bowled_b
    )
    if overs * session.balls_per_over <= balls_already_bowled:
        await update.message.reply_html("⚠️ New overs must leave at least one ball to bowl.")
        return

    session.max_overs = overs
    await _update_team_live_score(context, session)
    await update.message.reply_html(f"✅ First-innings length changed to <b>{overs} overs</b>.")
