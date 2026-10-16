from telegram import Update
from telegram.ext import ContextTypes
from ..services.permissions import permission_service

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not permission_service.is_admin(user.id):
        await update.message.reply_html("❌ You do not have admin permissions.")
        return

    text = (
        "🛡️ <b>Admin Controls:</b>\n"
        "• <code>/forcestart</code> - Force start an active lobby\n"
        "• <code>/cancel</code> - Cancel active match\n"
        "• <code>/broadcast &lt;msg&gt;</code> - Send announcement"
    )
    await update.message.reply_html(text)
