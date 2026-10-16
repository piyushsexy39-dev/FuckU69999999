from telegram import Update
from telegram.ext import ContextTypes
from ..services.tournament_service import tournament_service
from ..services.squad_service import squad_service

async def tournament_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🏆 <b>GULLY PREMIER LEAGUE (GPL)</b> 🏆\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to the prestigious street cricket championship!\n"
        "6 Franchises competing for the Mohalla Gold Trophy.\n\n"
        "Use <code>/tournament_standings</code> or <code>/fixtures</code> to track season 5!"
    )
    await update.message.reply_html(text)
