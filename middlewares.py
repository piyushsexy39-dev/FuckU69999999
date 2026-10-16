import logging
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("gullycric")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    err_str = str(context.error) if context.error else ""
    if "Message is not modified" in err_str or "Forbidden: bot was blocked" in err_str:
        return

    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update):
        if update.callback_query:
            try:
                await update.callback_query.answer("⚠️ An error occurred. Please try again.", show_alert=True)
            except Exception:
                pass
        elif update.effective_message:
            chat = update.effective_chat
            text = update.effective_message.text or ""
            # Only send error reply in private DM or explicit slash commands, never spam public groups
            if (chat and chat.type == "private") or text.startswith("/"):
                try:
                    await update.effective_message.reply_html(
                        "⚠️ <b>An error occurred while processing this action.</b>\nPlease try again."
                    )
                except Exception:
                    pass
