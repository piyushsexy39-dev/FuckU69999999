from ..config import config
import html
import logging
import os

from sqlalchemy import select
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ..db.models import MatchPlayerRecord, User
from ..db.session import async_session_factory
from ..services.economy import economy_service
from ..services.media_service import media_service

logger = logging.getLogger("gullycric.stats")


def _display_name(user: User) -> str:
    return user.first_name if user.first_name and user.first_name != "Player" else (user.username or "Player")


def get_stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="lb_overall")],
        [InlineKeyboardButton("👑 Sexy Owner!!", url=config.owner_contact_url())],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_home")],
    ])


def get_leaderboard_keyboard(current_filter: str = "overall") -> InlineKeyboardMarkup:
    def btn(label: str, key: str) -> InlineKeyboardButton:
        prefix = "• " if current_filter == key else ""
        return InlineKeyboardButton(f"{prefix}{label}", callback_data=f"lb_{key}")

    return InlineKeyboardMarkup([
        [btn("⭐ Overall", "overall"), btn("🏏 Runs", "runs"), btn("🎯 Wickets", "wickets")],
        [btn("💎 Diamonds", "wealth"), btn("🚀 Fours", "fours"), btn("6️⃣ Sixes", "sixes")],
        [btn("🦆 Ducks", "ducks"), btn("📉 Economy", "eco"), btn("⚡ Strike Rate", "strike_rate")],
        [InlineKeyboardButton("👑 Sexy Owner!!", url=config.owner_contact_url())],
        [btn("🔙 Back", "menu_stats")],
    ])


async def _send_stats_response(update: Update, text: str, keyboard: InlineKeyboardMarkup):
    banner_path = media_service.get_local_media_path("start_banner")
    query = update.callback_query
    if query:
        if banner_path and os.path.exists(banner_path):
            try:
                with open(banner_path, "rb") as photo:
                    await query.message.reply_photo(
                        photo=photo, caption=text, parse_mode="HTML", reply_markup=keyboard
                    )
                await query.message.delete()
                return
            except Exception:
                logger.exception("Could not send My Stats poster for callback")
        try:
            if query.message and query.message.photo:
                await query.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=keyboard)
            else:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            logger.exception("Could not update My Stats callback message")
        return

    if update.message:
        if banner_path and os.path.exists(banner_path):
            try:
                with open(banner_path, "rb") as photo:
                    await update.message.reply_photo(
                        photo=photo, caption=text, parse_mode="HTML", reply_markup=keyboard
                    )
                return
            except Exception:
                logger.exception("Could not send My Stats poster")
        await update.message.reply_html(text, reply_markup=keyboard)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    requester = update.effective_user
    if not requester:
        return

    target_id = requester.id
    target_name = requester.first_name
    target_username = requester.username
    if (
        update.message
        and getattr(update.message, "reply_to_message", None)
        and getattr(update.message.reply_to_message, "from_user", None)
        and isinstance(getattr(update.message.reply_to_message.from_user, "id", None), int)
    ):
        target = update.message.reply_to_message.from_user
        target_id, target_name, target_username = target.id, target.first_name, target.username
    elif context.args and context.args[0].lstrip("@").isdigit():
        target_id = int(context.args[0].lstrip("@"))
        target_name = "Player"

    user = await economy_service.get_or_create_user(target_id, target_username, target_name)
    played = user.matches_played or 0
    wins = user.matches_won or 0
    runs = user.total_runs or 0
    balls_faced = user.balls_faced or 0
    balls_bowled = user.balls_bowled or 0
    runs_conceded = user.runs_conceded or 0
    wickets = user.total_wickets or 0
    win_rate = wins / played * 100 if played else 0
    strike_rate = runs / balls_faced * 100 if balls_faced else None
    economy = runs_conceded * 6 / balls_bowled if balls_bowled else None

    async with async_session_factory() as session:
        records = (
            await session.execute(
                select(MatchPlayerRecord).where(MatchPlayerRecord.user_id == target_id)
            )
        ).scalars().all()

    fifties = sum(1 for record in records if 50 <= record.runs < 100)
    centuries = sum(1 for record in records if record.runs >= 100)
    recorded_solo = sum(1 for record in records if record.mode == "solo")
    recorded_team = sum(1 for record in records if record.mode == "team")
    name = html.escape(_display_name(user))
    lines = [
        "📊 <b>CRICKET CAREER STATISTICS</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"👤 Player: <b>{name}</b>",
        f"💎 Diamonds: <b>{user.diamonds or 0:,} 💎</b>",
        "",
        "🏏 <b>Batting:</b>",
        f"• Highest: <b>{user.highest_score or 0}</b>",
        f"• Total Runs: <b>{runs:,}</b>",
        f"• Runs per match: <b>{runs / played:.2f}</b>" if played else "• Runs per match: <b>0.00</b>",
        f"• Strike Rate: <b>{strike_rate:.2f}</b>" if strike_rate is not None else "• Strike Rate: <b>Not recorded</b>",
        f"• 6s: <b>{user.total_sixes or 0}</b> | 4s: <b>{user.total_fours or 0}</b>",
        f"• Recorded 100s: <b>{centuries}</b> | Recorded 50s: <b>{fifties}</b>",
        f"• Recorded ducks: <b>{user.total_ducks or 0}</b>",
        "",
        "🥎 <b>Bowling:</b>",
        f"• Wickets: <b>{wickets}</b>",
        f"• Overs: <b>{balls_bowled // 6}.{balls_bowled % 6}</b>",
        f"• Economy: <b>{economy:.2f}</b>" if economy is not None else "• Economy: <b>Not recorded</b>",
        "",
        "🏆 <b>Matches & Record:</b>",
        f"• Recorded Solo: <b>{recorded_solo}</b> | Team: <b>{recorded_team}</b>",
        f"• Played: <b>{played}</b> · Won: <b>{wins}W</b> · Lost: <b>{max(0, played - wins)}L</b>",
        f"• Win Rate: <b>{win_rate:.1f}%</b>",
        "• POTM, streaks, catches and hat-tricks: <b>Not stored in career data</b>",
    ]
    if len(records) < played:
        lines.append(
            "<i>Some older matches lack detailed records; recorded milestones, mode totals, "
            "strike rate and economy cover matches stored after detailed tracking was added.</i>"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    await _send_stats_response(update, "\n".join(lines), get_stats_keyboard())


def _is_real_player(user: User) -> bool:
    return bool(user and (user.started_bot or user.matches_played))


def _leaderboard_metric(user: User, key: str):
    if key == "runs":
        return user.total_runs or 0
    if key == "wickets":
        return user.total_wickets or 0
    if key == "fours":
        return user.total_fours or 0
    if key == "sixes":
        return user.total_sixes or 0
    if key == "ducks":
        return user.total_ducks or 0
    if key == "wealth":
        return user.diamonds or 0
    if key == "eco":
        return (user.runs_conceded * 6 / user.balls_bowled) if user.balls_bowled else None
    if key == "strike_rate":
        return (user.total_runs * 100 / user.balls_faced) if user.balls_faced else None
    if key == "wins":
        return user.matches_won or 0
    return (user.total_runs or 0) + (user.total_wickets or 0) * 10 + (user.matches_won or 0) * 25


def _rank_users(users, key: str):
    ranked = [(player, _leaderboard_metric(player, key)) for player in users if _is_real_player(player)]
    ranked = [(player, score) for player, score in ranked if score is not None]
    ranked.sort(key=lambda item: item[1], reverse=key != "eco")
    return ranked


async def leaderboard_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    filter_key: str = "overall",
):
    requester = update.effective_user
    if not requester:
        return
    current_user = await economy_service.get_or_create_user(
        requester.id, requester.username, requester.first_name
    )
    labels = {
        "runs": ("🏏 Runs", "runs"),
        "wickets": ("🎯 Wickets", "wickets"),
        "fours": ("4️⃣ Fours", "fours"),
        "sixes": ("🚀 Sixes", "sixes"),
        "ducks": ("🦆 Ducks", "ducks"),
        "wealth": ("💎 Diamonds", "Diamonds 💎"),
        "eco": ("📉 Economy", "runs per 6 balls"),
        "strike_rate": ("⚡ Strike Rate", "runs per 100 balls"),
        "wins": ("🏆 Wins", "wins"),
        "overall": ("⭐ Overall", "pts"),
    }
    if filter_key not in labels:
        filter_key = "overall"
    label, unit = labels[filter_key]

    async with async_session_factory() as session:
        users = (await session.execute(select(User))).scalars().all()

    users = [user for user in users if _is_real_player(user)]
    ranked = _rank_users(users, filter_key)
    user_rank = next(
        (index for index, (player, _) in enumerate(ranked, start=1)
         if player.user_id == current_user.user_id),
        None,
    )

    lines = [
        f"🏆 <b>LEADERBOARD · {label}</b>",
        "<i>Real recorded player statistics</i>\n",
    ]
    medals = ["🥇", "🥈", "🥉"]
    for index, (player, score) in enumerate(ranked[:20]):
        badge = medals[index] if index < len(medals) else f"{index + 1}."
        name = html.escape(_display_name(player))
        value = f"{score:,.2f}" if filter_key in ("eco", "strike_rate") else f"{score:,}"
        lines.append(f"{badge} <b>{name}</b> — <b>{value}</b> {unit}")
    if not ranked:
        lines.append("<i>No recorded results for this category yet.</i>")

    current_score = _leaderboard_metric(current_user, filter_key)
    if user_rank is None or current_score is None:
        lines.append("\n👤 Your rank: <b>Not ranked yet</b>")
    else:
        value = f"{current_score:,.2f}" if filter_key in ("eco", "strike_rate") else f"{current_score:,}"
        lines.append(f"\n👤 Your rank: <b>#{user_rank} of {len(ranked)}</b> — <b>{value}</b> {unit}")
    text = "\n".join(lines)
    keyboard = get_leaderboard_keyboard(filter_key)
    banner_path = media_service.get_local_media_path("start_banner")
    query = update.callback_query
    if query:
        if banner_path and os.path.exists(banner_path):
            try:
                with open(banner_path, "rb") as photo:
                    await query.message.reply_photo(
                        photo=photo, caption=text, parse_mode="HTML", reply_markup=keyboard
                    )
                await query.message.delete()
                return
            except Exception:
                logger.exception("Could not send leaderboard poster")
        try:
            if query.message and query.message.photo:
                await query.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=keyboard)
            else:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            logger.exception("Could not update leaderboard callback")
        return

    if update.message:
        if banner_path and os.path.exists(banner_path):
            try:
                with open(banner_path, "rb") as photo:
                    await update.message.reply_photo(
                        photo=photo, caption=text, parse_mode="HTML", reply_markup=keyboard
                    )
                return
            except Exception:
                logger.exception("Could not send leaderboard poster")
        await update.message.reply_html(text, reply_markup=keyboard)
