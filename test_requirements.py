import pytest
import pytest_asyncio
import json
import io
import asyncio
from unittest.mock import AsyncMock, MagicMock
from telegram import Update, Message, Chat, User
from gullycric.src.services.team_match_service import team_match_service, TeamMatchSession, TeamPlayer
from gullycric.src.services.backup_service import backup_service
from gullycric.src.handlers import team

def make_context():
    context = MagicMock()
    context.bot.send_photo = AsyncMock()
    context.bot.send_message = AsyncMock()
    context.bot.send_animation = AsyncMock()
    context.bot.edit_message_text = AsyncMock()
    return context

def make_command_update(chat_id: int, user_id: int, text: str = ""):
    update = MagicMock(spec=Update)
    update.effective_chat = MagicMock(spec=Chat, id=chat_id)
    update.effective_user = MagicMock(spec=User, id=user_id, first_name="User", username="user")
    update.message = MagicMock(spec=Message)
    update.message.message_id = 999
    update.message.text = text
    update.message.reply_html = AsyncMock()
    update.message.reply_to_message = None
    return update

@pytest.mark.asyncio
async def test_second_innings_non_striker_selection():
    chat_id = 9901
    session = TeamMatchSession(
        match_id="t_sec_bat",
        chat_id=chat_id,
        status="active",
        host_id=10,
        current_innings=2,
        striker_idx=0,
        non_striker_idx=None,
        striker_faced_ball=True,
        team_a_players=[TeamPlayer(user_id=1, name="Striker"), TeamPlayer(user_id=2, name="NonStriker")],
        batting_team="team_a",
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    update = make_command_update(chat_id, 10, "/batting 2")
    context.args = ["2"]
    
    try:
        await team.batting_command(update, context)
        assert session.non_striker_idx == 1
        assert session.striker_idx == 0
    finally:
        team_match_service.remove_session(chat_id)

@pytest.mark.asyncio
async def test_prevent_same_player_both_batting_positions():
    chat_id = 9902
    session = TeamMatchSession(
        match_id="t_dup_bat",
        chat_id=chat_id,
        status="active",
        host_id=10,
        striker_idx=0,
        non_striker_idx=None,
        team_a_players=[TeamPlayer(user_id=1, name="PlayerOne"), TeamPlayer(user_id=2, name="PlayerTwo")],
        batting_team="team_a",
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    update = make_command_update(chat_id, 10, "/batting 1")
    context.args = ["1"]
    
    try:
        await team.batting_command(update, context)
        assert "already at the crease" in update.message.reply_html.call_args[0][0]
    finally:
        team_match_service.remove_session(chat_id)

@pytest.mark.asyncio
async def test_match_start_blocked_few_players():
    chat_id = 9903
    session = TeamMatchSession(
        match_id="t_min_p",
        chat_id=chat_id,
        status="lobby",
        host_id=10,
        team_a_players=[TeamPlayer(user_id=1, name="A1")],
        team_b_players=[TeamPlayer(user_id=2, name="B1"), TeamPlayer(user_id=3, name="B2")],
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    query = MagicMock()
    query.from_user = MagicMock(id=10)
    query.data = "team_start_t_min_p"
    query.message = MagicMock(chat=MagicMock(id=chat_id))
    query.answer = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_chat = MagicMock(id=chat_id)
    update.effective_user = MagicMock(id=10)
    update.callback_query = query
    
    try:
        await team.team_callback_handler(update, context)
        query.answer.assert_awaited()
        assert "at least 2 players" in query.answer.call_args[0][0]
    finally:
        team_match_service.remove_session(chat_id)

@pytest.mark.asyncio
async def test_match_start_valid_players():
    chat_id = 9904
    session = TeamMatchSession(
        match_id="t_min_ok",
        chat_id=chat_id,
        status="lobby",
        host_id=10,
        team_a_players=[TeamPlayer(user_id=1, name="A1"), TeamPlayer(user_id=2, name="A2")],
        team_b_players=[TeamPlayer(user_id=3, name="B1"), TeamPlayer(user_id=4, name="B2")],
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    query = MagicMock()
    query.from_user = MagicMock(id=10)
    query.data = "team_start_t_min_ok"
    query.message = MagicMock(chat=MagicMock(id=chat_id))
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_chat = MagicMock(id=chat_id)
    update.effective_user = MagicMock(id=10)
    update.callback_query = query
    
    try:
        await team.team_callback_handler(update, context)
        assert session.status == "awaiting_overs"
    finally:
        team_match_service.remove_session(chat_id)

@pytest.mark.asyncio
async def test_backup_restore_signature_roundtrip():
    session = TeamMatchSession(
        match_id="t_bk_1",
        chat_id=9905,
        status="active",
        team_a_name="India",
        team_b_name="Australia",
        total_runs_a=120,
        wickets_a=3,
        balls_bowled_a=60,
        team_a_players=[TeamPlayer(user_id=1, name="Rohit"), TeamPlayer(user_id=2, name="Kohli")],
        team_b_players=[TeamPlayer(user_id=3, name="Starc"), TeamPlayer(user_id=4, name="Cummins")],
    )
    d = session.to_dict()
    signed_bytes = backup_service.generate_signed_backup(d)
    
    # Load and verify
    loaded = backup_service.verify_and_load_backup(signed_bytes)
    assert loaded["match_id"] == "t_bk_1"
    assert loaded["team_a_name"] == "India"
    assert loaded["total_runs_a"] == 120

@pytest.mark.asyncio
async def test_tampered_backup_rejection():
    session = TeamMatchSession(match_id="t_bk_2", chat_id=9906)
    signed_bytes = backup_service.generate_signed_backup(session.to_dict())
    
    # Tamper with json content
    data = json.loads(signed_bytes.decode("utf-8"))
    data["total_runs_a"] = 999
    tampered_bytes = json.dumps(data).encode("utf-8")
    
    with pytest.raises(ValueError, match="Cryptographic signature verification failed"):
        backup_service.verify_and_load_backup(tampered_bytes)

@pytest.mark.asyncio
async def test_missing_signature_rejection():
    d = {"match_id": "unsigned", "chat_id": 9907}
    unsigned_bytes = json.dumps(d).encode("utf-8")
    
    with pytest.raises(ValueError, match="Missing cryptographic signature"):
        backup_service.verify_and_load_backup(unsigned_bytes)

@pytest.mark.asyncio
async def test_captain_only_changecap():
    chat_id = 9908
    session = TeamMatchSession(
        match_id="t_cap",
        chat_id=chat_id,
        status="active",
        captain_a_id=10,
        captain_b_id=20,
        team_a_players=[TeamPlayer(user_id=10, name="CapA"), TeamPlayer(user_id=11, name="P2")],
        team_b_players=[TeamPlayer(user_id=20, name="CapB"), TeamPlayer(user_id=21, name="P3")],
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    
    # Non-captain tries to change cap A
    update = make_command_update(chat_id, 11, "/changecap a")
    context.args = ["a"]
    try:
        await team.change_cap_command(update, context)
        assert "Only Team A's current captain can change" in update.message.reply_html.call_args[0][0]
        
        # Real captain A changes cap A
        update_cap = make_command_update(chat_id, 10, "/changecap a")
        update_cap.message.reply_html.reset_mock()
        await team.change_cap_command(update_cap, context)
        assert session.captain_a_id == 11
    finally:
        team_match_service.remove_session(chat_id)

@pytest.mark.asyncio
async def test_host_only_remove_command():
    chat_id = 9909
    session = TeamMatchSession(
        match_id="t_rem",
        chat_id=chat_id,
        status="active",
        host_id=99,
        team_a_players=[TeamPlayer(user_id=10, name="Striker"), TeamPlayer(user_id=11, name="Bench")],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler")],
        striker_idx=0,
        current_bowler_id=20,
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    
    # Non-host tries /remove
    update_non_host = make_command_update(chat_id, 11, "/remove 11 A")
    context.args = ["11", "a"]
    try:
        await team.remove_player_command(update_non_host, context)
        assert "Only the match host can remove players!" in update_non_host.message.reply_html.call_args[0][0]
        
        # Host tries removing active striker (should be rejected)
        update_host_striker = make_command_update(chat_id, 99, "/remove 10 A")
        context.args = ["10", "a"]
        await team.remove_player_command(update_host_striker, context)
        assert "Cannot remove a batter currently at the crease!" in update_host_striker.message.reply_html.call_args[0][0]

        # Host tries removing active bowler (should be rejected)
        update_host_bowler = make_command_update(chat_id, 99, "/remove 20 B")
        context.args = ["20", "b"]
        await team.remove_player_command(update_host_bowler, context)
        assert "Cannot remove the current active bowler!" in update_host_bowler.message.reply_html.call_args[0][0]

        # Host successfully removes bench player
        update_host_bench = make_command_update(chat_id, 99, "/remove 11 A")
        context.args = ["11", "a"]
        await team.remove_player_command(update_host_bench, context)
        assert len(session.team_a_players) == 1
        assert session.team_a_players[0].user_id == 10
    finally:
        team_match_service.remove_session(chat_id)
