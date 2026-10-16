from telegram import Update
from telegram.ext import ContextTypes
from ..services.auction_service import auction_service

async def auction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔨 <b>GULLY PLAYER AUCTION ROOM</b> 🔨\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Place bids on premier mohalla players!\n"
        "To view active lots: <code>/lots</code>\n"
        "To place a bid: <code>/bid &lt;amount&gt;</code>"
    )
    await update.message.reply_html(text)
