from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_lobby_keyboard(match_id: str, is_host: bool = False, can_start: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("🏏 Join Match", callback_data=f"join_{match_id}"),
            InlineKeyboardButton("❌ Leave", callback_data=f"leave_{match_id}"),
        ]
    ]
    if is_host and can_start:
        buttons.append([InlineKeyboardButton("⚡ Force Start", callback_data=f"force_{match_id}")])
    return InlineKeyboardMarkup(buttons)

def get_batting_keyboard(match_id: str) -> InlineKeyboardMarkup:
    """Public batting number selection buttons (1 to 6)."""
    buttons = [
        [
            InlineKeyboardButton("1", callback_data=f"bat_{match_id}_1"),
            InlineKeyboardButton("2", callback_data=f"bat_{match_id}_2"),
            InlineKeyboardButton("3", callback_data=f"bat_{match_id}_3"),
        ],
        [
            InlineKeyboardButton("4", callback_data=f"bat_{match_id}_4"),
            InlineKeyboardButton("5", callback_data=f"bat_{match_id}_5"),
            InlineKeyboardButton("6", callback_data=f"bat_{match_id}_6"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)

def get_bowling_keyboard(match_id: str) -> InlineKeyboardMarkup:
    """Private bowling delivery choice (1 to 6)."""
    buttons = [
        [
            InlineKeyboardButton("1", callback_data=f"bowl_{match_id}_1"),
            InlineKeyboardButton("2", callback_data=f"bowl_{match_id}_2"),
            InlineKeyboardButton("3", callback_data=f"bowl_{match_id}_3"),
        ],
        [
            InlineKeyboardButton("4", callback_data=f"bowl_{match_id}_4"),
            InlineKeyboardButton("5", callback_data=f"bowl_{match_id}_5"),
            InlineKeyboardButton("6", callback_data=f"bowl_{match_id}_6"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)

def get_bowler_dm_prompt_keyboard(match_id: str, bot_username: str) -> InlineKeyboardMarkup:
    """Button in group for bowler to open DM or pick privately."""
    url = f"https://t.me/{bot_username}?start=bowl_{match_id}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎳 Choose Delivery in Private DM", url=url)],
        [InlineKeyboardButton("⚡ Secret Bowl (Inline Query)", switch_inline_query_current_chat=f"bowl_{match_id}_")]
    ])
