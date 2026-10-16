from telegram import Update
from telegram.ext import ContextTypes
from ..services.squad_service import squad_service

async def squads_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View active player squads."""
    teams = squad_service.get_all_teams()
    lines = [
        "📋 <b>ACTIVE PLAYER SQUADS</b> 📋",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not teams:
        lines.append("No team squads active right now.")
        lines.append("Play solo with <code>/solo</code> or open desk with <code>/start</code>!")
    else:
        for t in teams:
            lines.append(f"\n{t['badge']} <b>{t['name']}</b>")
            lines.append(f"👑 Captain: <b>{t['captain_name']}</b>")
            lines.append("Players: " + ", ".join(t["players"]))

    await update.message.reply_html("\n".join(lines))
