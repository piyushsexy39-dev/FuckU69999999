import pytest
from unittest.mock import AsyncMock, MagicMock
from gullycric.src.services.team_match_service import team_match_service, TeamMatchSession, TeamPlayer
from gullycric.src.handlers.team import (
    batting_command,
    bowling_command,
    handle_team_chat_input,
    run_team_bowler_timer,
    run_team_batter_timer,
    cancel_team_turn_timer,
)

@pytest.mark.asyncio
async def test_team_striker_and_non_striker_selection():
    chat_id = 998877
    session = team_match_service.create_session(chat_id, initiator_id=1001, initiator_name="HostGuy")
    session.status = "active"
    session.batting_team = "team_a"
    session.bowling_team = "team_b"
    session.captain_a_id = 1001
    session.captain_b_id = 2001

    session.add_player("a", 1001, "PlayerOne")
    session.add_player("a", 1002, "PlayerTwo")
    session.add_player("a", 1003, "PlayerThree")
    session.add_player("b", 2001, "BowlerOne")
    session.add_player("b", 2002, "BowlerTwo")

    chat = MagicMock(id=chat_id, type="group")
    user = MagicMock(id=1001, first_name="HostGuy")
    msg = MagicMock()
    msg.reply_html = AsyncMock()

    update = MagicMock()
    update.effective_chat = chat
    update.effective_user = user
    update.message = msg

    context = MagicMock()

    # Select striker (PlayerOne = idx 1)
    context.args = ["1"]
    await batting_command(update, context)
    assert session.striker_idx == 0
    assert session.non_striker_idx is None

    # Select non-striker (PlayerTwo = idx 2)
    context.args = ["2"]
    await batting_command(update, context)
    assert session.striker_idx == 0
    assert session.non_striker_idx == 1
    assert session.get_current_striker().name == "PlayerOne"
    assert session.get_current_non_striker().name == "PlayerTwo"

@pytest.mark.asyncio
async def test_strike_rotation_and_no_last_man_standing():
    chat_id = 998866
    session = team_match_service.create_session(chat_id, initiator_id=1001, initiator_name="HostGuy")
    session.status = "active"
    session.batting_team = "team_a"
    session.bowling_team = "team_b"
    session.captain_a_id = 1001
    session.captain_b_id = 2001

    p1 = TeamPlayer(user_id=1001, name="Striker")
    p2 = TeamPlayer(user_id=1002, name="NonStriker")
    session.team_a_players = [p1, p2]
    b1 = TeamPlayer(user_id=2001, name="Bowler")
    session.team_b_players = [b1]

    session.striker_idx = 0
    session.non_striker_idx = 1
    session.current_bowler_id = 2001

    # Strike rotation on odd runs:
    session.rotate_strike()
    assert session.striker_idx == 1
    assert session.non_striker_idx == 0
    assert session.get_current_striker().name == "NonStriker"

    # Rotate back
    session.rotate_strike()
    assert session.striker_idx == 0

    # No last man standing rule:
    # If 1 of 2 players is out, the team cannot continue batting without a partner!
    assert not session.is_all_out()
    p1.is_out = True
    session.wickets_a = 1
    assert session.is_all_out()

@pytest.mark.asyncio
async def test_potm_calculation():
    chat_id = 998855
    session = team_match_service.create_session(chat_id, initiator_id=1001, initiator_name="HostGuy")
    p1 = TeamPlayer(user_id=1001, name="BatterMVP", runs=45, balls=12, sixes=3, fours=4)
    p2 = TeamPlayer(user_id=2001, name="BowlerMVP", wickets=3, runs_conceded=15)
    session.team_a_players = [p1]
    session.team_b_players = [p2]

    potm = session.calculate_potm()
    assert potm is not None
    assert potm.name in ("BatterMVP", "BowlerMVP")

@pytest.mark.asyncio
async def test_team_backup_and_restore():
    session = TeamMatchSession(
        match_id="tm_55443",
        chat_id=123987,
        host_id=101,
        host_name="CaptainKirk",
        status="active",
        max_overs=2,
        balls_per_over=6,
        team_a_name="India 🇮🇳",
        team_b_name="Australia 🇦🇺",
        team_a_players=[
            TeamPlayer(user_id=101, name="Rohit", runs=25, balls=10),
            TeamPlayer(user_id=102, name="Kohli", runs=30, balls=15),
        ],
        team_b_players=[
            TeamPlayer(user_id=201, name="Starc", wickets=1, runs_conceded=18),
        ],
        striker_idx=0,
        non_striker_idx=1,
        current_bowler_id=201,
        total_runs_a=55,
        wickets_a=0,
        balls_bowled_a=15
    )

    session.sent_media_events.add("team-openers:1")
    data = session.to_dict()
    assert data["mode"] == "team"
    assert data["match_id"] == "tm_55443"
    assert len(data["team_a_players"]) == 2

    restored = TeamMatchSession.from_dict(data)
    assert restored.match_id == "tm_55443"
    assert restored.team_a_name == "India 🇮🇳"
    assert restored.get_current_striker().name == "Rohit"
    assert restored.get_current_non_striker().name == "Kohli"
    assert restored.get_current_bowler().name == "Starc"
    assert restored.total_runs_a == 55
    assert restored.sent_media_events == {"team-openers:1"}

@pytest.mark.asyncio
async def test_bowler_first_and_striker_order():
    from gullycric.src.handlers.team import handle_team_chat_input
    chat_id = 998844
    session = team_match_service.create_session(chat_id, initiator_id=1001, initiator_name="HostGuy")
    session.status = "active"
    session.batting_team = "team_a"
    session.bowling_team = "team_b"

    p1 = TeamPlayer(user_id=1001, name="Striker")
    p2 = TeamPlayer(user_id=1002, name="NonStriker")
    session.team_a_players = [p1, p2]
    b1 = TeamPlayer(user_id=2001, name="Bowler")
    session.team_b_players = [b1]

    session.striker_idx = 0
    session.non_striker_idx = 1
    session.current_bowler_id = 2001
    session.pending_bowler_pick = None

    # 1. Striker tries to bat before bowler bowls:
    chat = MagicMock(id=chat_id, type="group")
    user = MagicMock(id=1001, first_name="Striker")
    msg = MagicMock(text="4")
    msg.reply_html = AsyncMock()
    update = MagicMock(effective_chat=chat, effective_user=user, message=msg)
    context = MagicMock()

    await handle_team_chat_input(update, context)
    # Must reject/warn that bowler hasn't delivered yet
    assert session.pending_bowler_pick is None
    assert p1.runs == 0
    msg.reply_html.assert_called_once()
    assert "waiting for the bowler to deliver in DM first" in msg.reply_html.call_args[0][0]

    # 2. Random spectator (user 9999) tries to play:
    msg.reply_html.reset_mock()
    spectator = MagicMock(id=9999, first_name="RandomGuy")
    update_spectator = MagicMock(effective_chat=chat, effective_user=spectator, message=msg)
    await handle_team_chat_input(update_spectator, context)
    msg.reply_html.assert_not_called()

    # 3. Bowler delivers in DM (sets pending_bowler_pick = 2):
    session.pending_bowler_pick = 2

    # 4. Now striker plays "4":
    msg.reply_html.reset_mock()
    context.bot.send_message = AsyncMock()
    await handle_team_chat_input(update, context)
    assert p1.runs == 4

@pytest.mark.asyncio
async def test_solo_bowler_first_enforcement():
    from gullycric.src.services.match_service import match_service
    session = await match_service.create_solo_lobby(
        chat_id=112233,
        host_id=5001,
        host_name="SoloHost",
        balls_per_turn=6
    )
    p2 = match_service.engine.create_player(5002, "SoloBowler")
    session.players.append(p2)
    session.status = "active"
    session.pending_bowler_pick = None
    session.pending_batter_pick = None

    # Batter tries to bat before bowler bowls:
    res = await match_service.record_delivery_interaction(
        match_id=session.match_id,
        user_id=5001,
        pick=4,
        role="batter"
    )
    assert res["status"] == "error"
    assert session.pending_batter_pick is None

    # Bowler bowls in DM:
    res_b = await match_service.record_delivery_interaction(
        match_id=session.match_id,
        user_id=5002,
        pick=4,
        role="bowler"
    )
    assert res_b["status"] == "bowler_picked"
    assert session.pending_bowler_pick == 4

    # Now batter bats:
    res_bat = await match_service.record_delivery_interaction(
        match_id=session.match_id,
        user_id=5001,
        pick=4,
        role="batter"
    )
    assert res_bat["status"] == "ball_resolved"
