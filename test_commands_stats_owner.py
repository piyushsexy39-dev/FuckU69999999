from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gullycric.src.db import session as db_session
from gullycric.src.db.base import Base
from gullycric.src.db.models import GroupChat, MatchPlayerRecord, User
from gullycric.src.handlers import owner as owner_handlers
from gullycric.src.handlers import team as team_handlers
from gullycric.src.handlers.owner import OWNER_ID, _is_owner
from gullycric.src.handlers.start import get_start_keyboard
from gullycric.src.handlers.stats import (
    _display_name,
    _is_real_player,
    _rank_users,
    get_leaderboard_keyboard,
)
from gullycric.src.handlers import stats as stats_handlers
from gullycric.src.services import economy as economy_module
from gullycric.src.services.economy import economy_service
from gullycric.src.services.team_match_service import TeamMatchSession, team_match_service
from gullycric.src import bot as bot_module
from gullycric.src.bot import create_bot_application


def test_command_registration_and_start_owner_link(monkeypatch):
    monkeypatch.setattr(bot_module.config, "bot_token", "123456789:ABCDefGhIJKlmNoPQRsTUVwxyZ123456789")
    app = create_bot_application()
    commands = {
        command
        for handlers in app.handlers.values()
        for handler in handlers
        for command in getattr(handler, "commands", ())
    }
    assert "game" not in commands
    assert "help" not in commands
    assert {"endgame", "mystats", "leaderboard", "broadcast", "donate"} <= commands
    assert {
        "joinsolo", "leavesolo", "extend", "forcestart", "score", "restore",
        "teams", "setovers", "changeover", "toss", "batting", "bowling",
        "forceadd", "changecap", "changehost",
    } <= commands

    buttons = [
        button
        for row in get_start_keyboard().inline_keyboard
        for button in row
    ]
    owner_button = next(button for button in buttons if button.text == "SexyOwner!!")
    assert owner_button.url == f"tg://user?id={OWNER_ID}"
    assert all(button.text != "📖 Help & Rules" for button in buttons)


def test_application_requires_configured_token(monkeypatch):
    monkeypatch.setattr(bot_module.config, "bot_token", "")

    with pytest.raises(ValueError, match="BOT_TOKEN must be configured"):
        create_bot_application()


def test_leaderboard_rankings_use_real_rates_and_unicode_names():
    economical = User(
        user_id=1, first_name="李小龙 🏏", runs_conceded=4, balls_bowled=6,
        total_runs=30, balls_faced=10, started_bot=True,
    )
    expensive = User(
        user_id=2, first_name="Batter", runs_conceded=12, balls_bowled=6,
        total_runs=20, balls_faced=10, matches_played=1,
    )
    no_bowling_data = User(user_id=3, first_name="New player")

    eco_order = _rank_users([expensive, no_bowling_data, economical], "eco")
    strike_rate_order = _rank_users([economical, expensive], "strike_rate")

    assert [player.user_id for player, _ in eco_order] == [1, 2]
    assert eco_order[0][1] == 4
    assert [player.user_id for player, _ in strike_rate_order] == [1, 2]
    assert _display_name(economical) == "李小龙 🏏"
    filters = {
        button.callback_data.removeprefix("lb_")
        for row in get_leaderboard_keyboard().inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("lb_")
    }
    assert {"runs", "wickets", "fours", "sixes", "ducks", "wealth", "eco", "strike_rate"} <= filters
    assert not _is_real_player(no_bowling_data)


def test_owner_identity_is_enforced():
    assert _is_owner(SimpleNamespace(id=OWNER_ID))
    assert not _is_owner(SimpleNamespace(id=123456789))
    assert not _is_owner(None)


@pytest.mark.asyncio
async def test_broadcast_reaches_started_users_and_active_groups_and_reports_failures(
    tmp_path, monkeypatch
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'broadcast.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(owner_handlers, "async_session_factory", factory)

    async with factory() as session:
        session.add_all([
            User(user_id=101, first_name="DM user", started_bot=True),
            User(user_id=102, first_name="No DM", started_bot=False),
            GroupChat(chat_id=-1001, title="Active group", is_active=True),
            GroupChat(chat_id=-1002, title="Failing group", is_active=True),
            GroupChat(chat_id=-1003, title="Inactive group", is_active=False),
        ])
        await session.commit()

    attempted_chat_ids = []

    async def send_message(chat_id, **_kwargs):
        attempted_chat_ids.append(chat_id)
        if chat_id == -1002:
            raise RuntimeError("bot was removed")

    message = SimpleNamespace(reply_html=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=OWNER_ID),
        message=message,
    )
    context = SimpleNamespace(
        args=["Match", "tonight"],
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=send_message)),
    )

    try:
        await owner_handlers.broadcast_command(update, context)
    finally:
        await engine.dispose()

    assert attempted_chat_ids == [101, -1001, -1002]
    summary = message.reply_html.await_args_list[0].args[0]
    assert "DMs: <b>1/1</b> sent · <b>0</b> failed" in summary
    assert "Groups: <b>1/2</b> sent · <b>1</b> failed" in summary
    assert "Group <code>-1002</code>: bot was removed" in message.reply_html.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_database_initialization_preserves_existing_users_and_adds_columns(
    tmp_path, monkeypatch
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    monkeypatch.setattr(db_session, "engine", engine)
    async with engine.begin() as connection:
        await connection.execute(text(
            "CREATE TABLE users (user_id INTEGER PRIMARY KEY, username VARCHAR(64), "
            "first_name VARCHAR(128), diamonds INTEGER)"
        ))
        await connection.execute(text(
            "INSERT INTO users (user_id, username, first_name, diamonds) "
            "VALUES (7, 'legacy', 'Legacy Player', 321)"
        ))

    await db_session.init_db()
    async with engine.connect() as connection:
        user = (await connection.execute(
            text("SELECT user_id, first_name, diamonds FROM users WHERE user_id = 7")
        )).one()
        columns = await connection.run_sync(
            lambda sync_connection: {column["name"] for column in inspect(sync_connection).get_columns("users")}
        )
    await engine.dispose()

    assert tuple(user) == (7, "Legacy Player", 321)
    assert {
        "total_ducks", "balls_faced", "balls_bowled", "runs_conceded", "started_bot"
    } <= columns


@pytest.mark.asyncio
async def test_match_and_reward_records_are_persistently_idempotent(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stats.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(
        economy_module,
        "async_session_factory",
        async_sessionmaker(engine, expire_on_commit=False),
    )

    stats = {
        "user_id": 99,
        "is_win": True,
        "runs": 42,
        "wickets": 2,
        "fours": 3,
        "sixes": 1,
        "match_id": "solo:persistent-test",
        "mode": "solo",
        "display_name": "Test Batter",
        "balls_faced": 20,
        "balls_bowled": 12,
        "runs_conceded": 9,
    }
    assert await economy_service.record_match_stats(**stats)
    assert not await economy_service.record_match_stats(**stats)
    assert await economy_service.add_rewards(99, diamonds=50, idempotency_key="reward:test:99")
    assert not await economy_service.add_rewards(99, diamonds=50, idempotency_key="reward:test:99")

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = await session.get(User, 99)
        record_count = (await session.execute(select(MatchPlayerRecord))).scalars().all()
    await engine.dispose()

    assert user.total_runs == 42
    assert user.matches_played == 1
    assert user.balls_faced == 20
    assert user.runs_conceded == 9
    assert user.diamonds == 304
    assert len(record_count) == 1


@pytest.mark.asyncio
async def test_owner_can_add_unlimited_diamonds_to_themselves(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'donations.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(
        owner_handlers,
        "async_session_factory",
        async_sessionmaker(engine, expire_on_commit=False),
    )
    message = SimpleNamespace(reply_html=AsyncMock(), entities=[], reply_to_message=None)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=OWNER_ID),
        message=message,
    )
    context = SimpleNamespace(args=[str(OWNER_ID), "1000000"])

    await owner_handlers.donate_command(update, context)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = await session.get(User, OWNER_ID)
    await engine.dispose()

    assert user.diamonds == 1_000_100
    assert message.reply_html.await_count == 1


@pytest.mark.asyncio
async def test_stats_and_leaderboard_buttons_match_their_commands(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'responses.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(economy_module, "async_session_factory", factory)
    monkeypatch.setattr(stats_handlers, "async_session_factory", factory)
    poster_keys = []
    monkeypatch.setattr(
        stats_handlers.media_service,
        "get_local_media_path",
        lambda key: poster_keys.append(key) or None,
    )
    async with factory() as session:
        session.add(User(
            user_id=987654321,
            username=None,
            first_name="क्रिकेट Fan 🏏",
            diamonds=500,
            matches_played=1,
            matches_won=1,
            total_runs=42,
            total_wickets=2,
            total_fours=3,
            total_sixes=1,
            total_ducks=0,
            balls_faced=20,
            balls_bowled=12,
            runs_conceded=9,
        ))
        session.add(MatchPlayerRecord(
            match_id="solo:stats-response",
            user_id=987654321,
            display_name="क्रिकेट Fan 🏏",
            mode="solo",
            runs=42,
            wickets=2,
            fours=3,
            sixes=1,
            balls_faced=20,
            balls_bowled=12,
            runs_conceded=9,
            is_win=True,
        ))
        await session.commit()

    user = SimpleNamespace(id=987654321, first_name="क्रिकेट Fan 🏏", username=None)
    context = SimpleNamespace(args=[])
    command_message = SimpleNamespace(reply_to_message=None, reply_html=AsyncMock())
    await stats_handlers.stats_command(
        SimpleNamespace(effective_user=user, message=command_message, callback_query=None),
        context,
    )
    stats_query = SimpleNamespace(
        message=SimpleNamespace(photo=()),
        edit_message_text=AsyncMock(),
    )
    await stats_handlers.stats_command(
        SimpleNamespace(effective_user=user, message=None, callback_query=stats_query),
        context,
    )
    assert command_message.reply_html.await_args.args[0] == stats_query.edit_message_text.await_args.args[0]

    leaderboard_message = SimpleNamespace(reply_html=AsyncMock())
    await stats_handlers.leaderboard_command(
        SimpleNamespace(effective_user=user, message=leaderboard_message, callback_query=None),
        context,
    )
    leaderboard_query = SimpleNamespace(
        message=SimpleNamespace(photo=()),
        edit_message_text=AsyncMock(),
    )
    await stats_handlers.leaderboard_command(
        SimpleNamespace(effective_user=user, message=None, callback_query=leaderboard_query),
        context,
    )
    command_leaderboard = leaderboard_message.reply_html.await_args.args[0]
    button_leaderboard = leaderboard_query.edit_message_text.await_args.args[0]
    assert command_leaderboard == button_leaderboard
    assert "क्रिकेट Fan 🏏" in command_leaderboard
    assert "987654321" not in command_leaderboard
    assert poster_keys == ["start_banner"] * 4
    await engine.dispose()


@pytest.mark.asyncio
async def test_team_endgame_confirms_and_saves_backup_before_removing_match():
    chat_id = -100123
    session = TeamMatchSession(
        match_id="tm_12345",
        chat_id=chat_id,
        host_id=7,
        host_name="Host",
    )
    team_match_service.active_sessions[chat_id] = session
    try:
        command_message = SimpleNamespace(reply_html=AsyncMock())
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=chat_id),
            effective_user=SimpleNamespace(id=7),
            message=command_message,
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_document=AsyncMock()))
        await team_handlers.end_match_command(update, context)
        keyboard = command_message.reply_html.await_args.kwargs["reply_markup"]
        callback_data = {
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        }
        assert callback_data == {
            f"team_endgame_yes_{session.match_id}",
            f"team_endgame_no_{session.match_id}",
        }

        callback_message = SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            message_id=55,
            delete=AsyncMock(),
        )
        query = SimpleNamespace(
            data=f"team_endgame_yes_{session.match_id}",
            message=callback_message,
            answer=AsyncMock(),
        )
        callback_update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(id=chat_id),
        )
        await team_handlers.team_callback_handler(callback_update, context)

        context.bot.send_document.assert_awaited_once()
        callback_message.delete.assert_awaited_once()
        assert team_match_service.get_session(chat_id) is None
    finally:
        team_match_service.remove_session(chat_id)
