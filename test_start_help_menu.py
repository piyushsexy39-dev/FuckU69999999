import pytest
from unittest.mock import AsyncMock, MagicMock
from gullycric.src.handlers.start import start_command, help_command, menu_callback_handler
from gullycric.src.services.economy import economy_service
from gullycric.src.services.team_match_service import team_match_service

@pytest.mark.asyncio
async def test_start_and_help_commands(monkeypatch):
    register_user = AsyncMock()
    monkeypatch.setattr(economy_service, "get_or_create_user", register_user)
    chat = MagicMock(id=12345, type="private")
    user = MagicMock(id=98765, first_name="CricketFan", username="cfan")
    msg = MagicMock()
    msg.reply_html = AsyncMock()

    update = MagicMock()
    update.effective_chat = chat
    update.effective_user = user
    update.effective_message = msg
    update.message = msg
    update.callback_query = None

    context = MagicMock()
    context.args = []

    # 1. Test /start
    await start_command(update, context)
    register_user.assert_awaited_once_with(
        user_id=98765,
        username="cfan",
        first_name="CricketFan",
        started_bot=True,
    )
    assert msg.reply_html.called
    start_call_args = msg.reply_html.call_args
    assert "ULTIMATE CRICKET ARENA" in start_call_args[0][0]
    assert start_call_args[1]["reply_markup"] is not None

    # 2. Test /help
    msg.reply_html.reset_mock()
    await help_command(update, context)
    assert msg.reply_html.called
    help_call_args = msg.reply_html.call_args
    assert "ULTIMATE CRICKET HELP DESK" in help_call_args[0][0]

@pytest.mark.asyncio
async def test_menu_callbacks():
    chat = MagicMock(id=12345, type="private")
    user = MagicMock(id=98765, first_name="CricketFan", username="cfan")
    msg = MagicMock()
    msg.edit_text = AsyncMock()

    query = MagicMock()
    query.edit_message_text = AsyncMock()
    query.answer = AsyncMock()
    query.message = msg

    update = MagicMock()
    update.effective_chat = chat
    update.effective_user = user
    update.callback_query = query

    context = MagicMock()

    callbacks = [
        "menu_play",
        "menu_stats",
        "menu_leaderboard",
        "menu_help",
        "help_solo",
        "help_team",
        "help_captains",
        "help_commands",
        "menu_home"
    ]

    for cb in callbacks:
        query.data = cb
        await menu_callback_handler(update, context)
        assert query.answer.called
