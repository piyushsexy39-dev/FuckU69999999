import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

from gullycric.src.engine.match_engine import PlayerSessionStats
from gullycric.src.handlers import solo, team
from gullycric.src.services.match_service import MatchSession, match_service
from gullycric.src.services.permissions import permission_service
from gullycric.src.services.team_match_service import (
    TeamMatchSession,
    TeamPlayer,
    team_match_service,
)


def make_context():
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=700)),
        send_photo=AsyncMock(),
        send_video=AsyncMock(),
        send_document=AsyncMock(),
        pin_chat_message=AsyncMock(),
        edit_message_text=AsyncMock(),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
    )
    return SimpleNamespace(bot=bot, args=[])


def make_command_update(chat_id, user_id, args=()):
    message = SimpleNamespace(reply_html=AsyncMock())
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
        effective_user=SimpleNamespace(id=user_id, first_name="Player"),
        message=message,
    )


def make_callback_update(chat_id, user_id, data):
    query = SimpleNamespace(
        data=data,
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=701),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
        effective_user=SimpleNamespace(id=user_id, first_name="Player"),
    )


@pytest.mark.asyncio
async def test_solo_and_team_creation_share_an_atomic_group_lock():
    chat_id = 881001

    async def create_solo():
        return await match_service.create_solo_lobby(chat_id, 10, "Host")

    async def create_team():
        return team_match_service.create_session(chat_id, 20, "Host")

    try:
        solo_result, team_result = await asyncio.gather(create_solo(), create_team())
        assert (solo_result is None) != (team_result is None)
        if solo_result:
            assert match_service.get_active_session_for_chat(chat_id) is solo_result
        else:
            assert team_match_service.get_session(chat_id) is team_result
    finally:
        for match_id, session in list(match_service.active_sessions.items()):
            if session.chat_id == chat_id:
                match_service.active_sessions.pop(match_id, None)
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
async def test_solo_lineup_and_bowling_figures_survive_batter_rotation():
    first = PlayerSessionStats(user_id=1, name="First", has_batted=True)
    next_batter = PlayerSessionStats(
        user_id=2, name="Next", wickets=2, runs_conceded=11, balls_bowled=8
    )
    next_bowler = PlayerSessionStats(user_id=3, name="Bowler")
    session = MatchSession(
        match_id="solo-lineup-test",
        chat_id=881002,
        host_id=1,
        host_name="First",
        status="active",
        players=[first, next_batter, next_bowler],
        current_batter_idx=0,
        current_bowler_idx=1,
    )

    assert match_service._advance_to_next_batter(session) is False
    assert session.current_batter is next_batter
    assert next_batter.balls_bowled == 8
    assert next_batter.wickets == 2
    assert next_batter.runs_conceded == 11
    assert session.current_batter.user_id != session.current_bowler.user_id

    lineup = solo.render_solo_lineup(session)
    assert "1. <b>First</b>" in lineup
    assert "2. <b>Next</b> · CURRENT BATTER" in lineup
    assert "Bowling order (current rotation)" in lineup


@pytest.mark.asyncio
async def test_batter_entry_media_is_deduplicated(monkeypatch):
    session = MatchSession(
        match_id="crease-entry-dedupe",
        chat_id=881020,
        host_id=1,
        host_name="Player",
    )
    context = make_context()
    send_media = AsyncMock()
    from gullycric.src.handlers import solo
    monkeypatch.setattr(solo, "send_event_media", send_media)
    await solo.send_batter_entry_media(context, session, "solo:1", "Player")
    await solo.send_batter_entry_media(context, session, "solo:1", "Player")
    send_media.assert_awaited_once()
    assert send_media.await_args.kwargs["event_key"] == "crease_entry"


def test_spamfree_blocks_third_consecutive_pick_and_resets_on_change():
    session = SimpleNamespace(
        spamfree=True,
        spamfree_last_bowler_id=None,
        spamfree_last_bowler_pick=None,
        spamfree_consecutive_count=0,
    )

    for _ in range(2):
        assert not solo.should_reject_spamfree_pick(session, 20, 4)
        solo.record_spamfree_pick(session, 20, 4)
    assert solo.should_reject_spamfree_pick(session, 20, 4)

    assert not solo.should_reject_spamfree_pick(session, 20, 5)
    solo.record_spamfree_pick(session, 20, 5)
    assert session.spamfree_consecutive_count == 1
    assert not solo.should_reject_spamfree_pick(session, 21, 5)


@pytest.mark.asyncio
async def test_milestone_celebration_uses_bundled_video():
    context = make_context()
    player = SimpleNamespace(user_id=21, name="Batter")

    await solo.send_run_milestone_celebration(context, 881027, player, 50)

    video = context.bot.send_video.await_args.kwargs["video"]
    assert video.name == solo.MILESTONE_VIDEO_PATH
    assert "50 RUNS COMPLETED" in context.bot.send_video.await_args.kwargs["caption"]


def test_team_over_summary_formats_balls_and_batter_runs_with_id_links():
    summary = {
        "over_number": 1,
        "bowler_mention": team.mention(TeamPlayer(user_id=20, name="Vimal vm")),
        "runs": 19,
        "wickets": 0,
        "deliveries": [
            {"batter_id": 10, "batter_name": "Sonu", "runs": 4, "symbol": "4"},
            {"batter_id": 10, "batter_name": "Sonu", "runs": 1, "symbol": "1"},
            {"batter_id": 10, "batter_name": "Sonu", "runs": 3, "symbol": "3"},
            {"batter_id": 11, "batter_name": "Gourab", "runs": 4, "symbol": "4"},
            {"batter_id": 11, "batter_name": "Gourab", "runs": 5, "symbol": "5"},
            {"batter_id": 11, "batter_name": "Gourab", "runs": 2, "symbol": "2"},
        ],
    }

    text = team._render_team_over_summary(None, summary)

    assert "OVER 1 COMPLETE" in text
    assert "<a href=\"tg://user?id=20\">Vimal vm</a>" in text
    assert "19 Runs • 0 Wickets" in text
    assert "4 • 1 • 3 • 4 • 5 • 2" in text
    assert "<a href=\"tg://user?id=10\">Sonu</a> — 8 (3) | SR 266.67" in text
    assert "<a href=\"tg://user?id=11\">Gourab</a> — 11 (3) | SR 366.67" in text


def test_team_poster_assets_resolve_from_nested_handler_directory(tmp_path, monkeypatch):
    poster_names = (
        "team_score_poster.jpg",
        "team_batting_poster.jpg",
        "team_bowling_poster.jpg",
    )
    for poster_name in poster_names:
        (tmp_path / poster_name).write_bytes(b"poster")

    monkeypatch.setattr(
        team,
        "__file__",
        str(tmp_path / "gullycric" / "src" / "handlers" / "team.py"),
    )

    for poster_name in poster_names:
        assert team._team_asset_path(poster_name) == str(tmp_path / poster_name)


@pytest.mark.asyncio
async def test_team_over_followup_is_separate_and_uses_actual_swap_state():
    session = TeamMatchSession(
        match_id="team-over-followup-test",
        chat_id=881029,
        status="active",
        bowling_team="team_b",
        captain_b_id=20,
        team_b_players=[TeamPlayer(user_id=20, name="Bowling Captain")],
        team_a_players=[TeamPlayer(user_id=10, name="Current Striker")],
        striker_idx=0,
    )
    summary = {"over_number": 2, "ends_swapped": True}
    context = make_context()

    await team._send_team_over_followup(context, session, summary)

    context.bot.send_message.assert_awaited_once()
    text = context.bot.send_message.await_args.kwargs["text"]
    assert "OVER 2 COMPLETE!" in text
    assert "Ends swapped! <a href=\"tg://user?id=10\">Current Striker</a> is on strike." in text
    assert "Bowling Captain <a href=\"tg://user?id=20\">Bowling Captain</a> (or Host)" in text
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_video.assert_not_awaited()

def test_team_final_scorecard_uses_player_figures_and_configured_over_rate():
    session = TeamMatchSession(
        match_id="team-final-scorecard-test",
        chat_id=881028,
        balls_per_over=6,
        team_a_name="Blue",
        team_b_name="Red",
        team_a_players=[
            TeamPlayer(
                user_id=31,
                name="Batter One",
                runs=30,
                balls=12,
                fours=3,
                wickets=2,
                runs_conceded=9,
                balls_bowled=6,
            ),
            TeamPlayer(user_id=32, name="Batter Two"),
        ],
        team_b_players=[
            TeamPlayer(user_id=41, name="Batter Three", runs=15, balls=10),
            TeamPlayer(user_id=42, name="Bowler Two", wickets=1, runs_conceded=12, balls_bowled=6),
        ],
        total_runs_a=40,
        wickets_a=3,
        balls_bowled_a=7,
        total_runs_b=35,
        wickets_b=4,
        balls_bowled_b=12,
    )

    text = team._render_team_final_scoreboard(session, "🏆 Blue won by 5 runs")

    assert "Match Summary !!⚡" in text
    assert "Blue Scorecard: 40/3" in text
    assert "└ ⏳ 1.1 Ov • RR 34.29" in text
    assert "Red Scorecard: 35/4" in text
    assert '<a href="tg://user?id=31">Batter One</a>' in text
    assert "├ 🏏 30(12) | SR 250.0" in text
    assert "├ 🥎 2W | 9R | 1.0ov | Eco 9.0" in text
    assert "├ 🥎 No bowling data" in text
    assert "Top batter:" in text
    assert "Best bowler:" in text


@pytest.mark.asyncio
async def test_team_score_command_sends_second_innings_poster_with_chase_stats():
    chat_id = 881025
    session = TeamMatchSession(
        match_id="team-score-chase-test",
        chat_id=chat_id,
        status="active",
        max_overs=10,
        balls_per_over=6,
        batting_team="team_b",
        current_innings=2,
        target=90,
        total_runs_b=85,
        wickets_b=2,
        balls_bowled_b=38,
        team_a_players=[TeamPlayer(user_id=20, name="Vimal vm", wickets=1, runs_conceded=22, balls_bowled=8)],
        team_b_players=[
            TeamPlayer(user_id=10, name="Sonu", runs=40, balls=10),
            TeamPlayer(user_id=11, name="Gourab", runs=20, balls=5),
            TeamPlayer(user_id=12, name="Out", is_out=True),
            TeamPlayer(user_id=13, name="Bench One"),
            TeamPlayer(user_id=14, name="Bench Two"),
        ],
        striker_idx=0,
        non_striker_idx=1,
        current_bowler_id=20,
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    update = make_command_update(chat_id, 10)
    update.message.message_id = 702
    try:
        await team.score_command(update, context)
        caption = context.bot.send_photo.await_args.kwargs["caption"]
        assert context.bot.send_photo.await_args.kwargs["photo"].name == team.TEAM_SCORE_POSTER_PATH
        assert "SCORE - 85/2 · 6.2 Ov" in caption
        assert "Team B · 2nd Inn" in caption
        assert "Need 6 runs to win · 2 wickets in hand · 22 balls left" in caption
        assert "<a href=\"tg://user?id=10\">Sonu</a>" in caption
        assert "@Sonu" not in caption
        pinned = team._render_team_live_score(session)
        assert "Sonu</a> — 40 (10b) · SR 400.0" in pinned
        assert "Gourab</a> — 20 (5b) · SR 400.0" in pinned
        assert "Vimal vm</a> — 1/22 · 1.2 Ov · Eco 16.5" in pinned
    finally:
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
async def test_teams_and_batting_commands_use_posters_and_saved_name_links():
    chat_id = 881026
    session = TeamMatchSession(
        match_id="team-lineup-poster-test",
        chat_id=chat_id,
        host_id=10,
        host_name="Host Name",
        status="active",
        batting_team="team_a",
        captain_a_id=10,
        team_a_players=[
            TeamPlayer(user_id=10, name="Real First Name"),
            TeamPlayer(user_id=11, name="Another Player"),
        ],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler Name")],
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    update = make_command_update(chat_id, 10)
    update.message.message_id = 703
    try:
        await team.team_command(update, context)
        await team.batting_command(update, context)

        photos = context.bot.send_photo.await_args_list
        assert [call.kwargs["photo"].name for call in photos] == [
            team.TEAM_SCORE_POSTER_PATH,
            team.TEAM_BATTING_POSTER_PATH,
        ]
        assert team.TEAM_SCORE_POSTER_URL == "https://kommodo.ai/i/8BiSmprYnZzpBscqSiLK"
        assert team.TEAM_BATTING_POSTER_URL == "https://kommodo.ai/i/2Oa9Tv0MpZE0JMMWhPzV"
        assert team.TEAM_BOWLING_POSTER_URL == "https://kommodo.ai/i/RwXfldrITOr7iCL2f4fl"
        assert "🅰️ <b>Team A 🔵</b>" in photos[1].kwargs["caption"]
        assert '└ 🧢 Cap: <a href="tg://user?id=10">Real First Name</a>' in photos[1].kwargs["caption"]
        assert "At crease: 0 · Out: 0 · To come: 2" in photos[1].kwargs["caption"]
        assert "TEAM MATCH · LIVE SCORE" not in photos[0].kwargs["caption"]
        assert '<a href="tg://user?id=10">Real First Name</a>' in photos[0].kwargs["caption"]
        assert '<a href="tg://user?id=11">Another Player</a>' in photos[1].kwargs["caption"]
        assert "@Real First Name" not in photos[0].kwargs["caption"]
    finally:
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
async def test_spamfree_command_supports_solo_host_and_team_captain():
    solo_chat_id = 881023
    team_chat_id = 881024
    solo_session = MatchSession(
        match_id="solo-spamfree-toggle",
        chat_id=solo_chat_id,
        host_id=10,
        host_name="Host",
        status="active",
        spamfree_last_bowler_id=20,
        spamfree_last_bowler_pick=3,
        spamfree_consecutive_count=2,
    )
    team_session = TeamMatchSession(
        match_id="team-spamfree-toggle",
        chat_id=team_chat_id,
        host_id=30,
        status="active",
        captain_a_id=31,
        captain_b_id=32,
    )
    match_service.active_sessions[solo_session.match_id] = solo_session
    team_match_service.active_sessions[team_chat_id] = team_session
    try:
        await team.spamfree_command(make_command_update(solo_chat_id, 10), make_context())
        assert solo_session.spamfree
        assert solo_session.spamfree_consecutive_count == 0

        await team.spamfree_command(make_command_update(team_chat_id, 31), make_context())
        assert team_session.spamfree

        unauthorized_update = make_command_update(team_chat_id, 99)
        await team.spamfree_command(unauthorized_update, make_context())
        assert team_session.spamfree
        unauthorized_update.message.reply_html.assert_awaited_once()
    finally:
        match_service.active_sessions.pop(solo_session.match_id, None)
        team_match_service.remove_session(team_chat_id)


@pytest.mark.asyncio
async def test_team_openers_media_only_follows_confirmed_pair(monkeypatch):
    chat_id = 881021
    session = TeamMatchSession(
        match_id="team-openers-test",
        chat_id=chat_id,
        host_id=10,
        status="active",
        batting_team="team_a",
        bowling_team="team_b",
        captain_a_id=10,
        team_a_players=[
            TeamPlayer(user_id=10, name="Striker"),
            TeamPlayer(user_id=11, name="Non-striker"),
        ],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler")],
        current_bowler_id=20,
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    context.args = ["1"]
    update = make_command_update(chat_id, 10)
    send_media = AsyncMock()
    monkeypatch.setattr(team, "send_event_media", send_media)
    from gullycric.src.handlers import solo
    monkeypatch.setattr(solo, "send_event_media", send_media)
    monkeypatch.setattr(team, "_update_team_live_score", AsyncMock())
    monkeypatch.setattr(team, "_start_delivery", AsyncMock())
    try:
        await team.batting_command(update, context)
        assert not any(call.kwargs.get("event_key") == "team_openers" for call in send_media.await_args_list)

        context.args = ["2"]
        await team.batting_command(update, context)
        event_keys = [call.kwargs.get("event_key") for call in send_media.await_args_list]
        assert event_keys.count("crease_entry") == 2
        assert "team_openers" not in event_keys
        crease_captions = [
            call.kwargs.get("caption") or call.args[3]
            for call in send_media.await_args_list
            if call.kwargs.get("event_key") == "crease_entry"
        ]
        assert '<a href="tg://user?id=10">Striker</a>' in crease_captions[0]
        assert '<a href="tg://user?id=11">Non-striker</a>' in crease_captions[1]
        assert any(
            "BATTING DUO AT THE CREASE" in call.kwargs.get("text", "")
            for call in context.bot.send_message.await_args_list
        )

        await team.batting_command(update, context)
        assert [call.kwargs.get("event_key") for call in send_media.await_args_list].count("team_openers") == 0
    finally:
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("batter_runs", "expected_media"),
    [(0, "duck"), (5, "wicket")],
)
async def test_team_dismissal_uses_batter_innings_runs_for_duck_media(
    monkeypatch, batter_runs, expected_media
):
    chat_id = 881022 + batter_runs
    striker = TeamPlayer(user_id=10, name="Batter", runs=batter_runs)
    session = TeamMatchSession(
        match_id=f"team-duck-test-{batter_runs}",
        chat_id=chat_id,
        host_id=10,
        status="active",
        batting_team="team_a",
        bowling_team="team_b",
        team_a_players=[
            striker,
            TeamPlayer(user_id=11, name="Non-striker"),
            TeamPlayer(user_id=12, name="Next batter"),
        ],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler")],
        striker_idx=0,
        non_striker_idx=1,
        current_bowler_id=20,
        pending_bowler_pick=4,
        total_runs_a=25,
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
        effective_user=SimpleNamespace(id=10, first_name="Batter"),
        message=SimpleNamespace(text="4", reply_html=AsyncMock(), message_id=701),
    )
    send_media = AsyncMock()
    monkeypatch.setattr(team, "send_event_media", send_media)
    monkeypatch.setattr(team, "_update_team_live_score", AsyncMock())
    try:
        await team.handle_team_chat_input(update, context)
        assert send_media.await_args.kwargs["event_key"] == expected_media
        assert session.total_runs_a == 25
        assert striker.is_out
    finally:
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
async def test_team_batter_afk_is_resolved_and_waits_for_captain_selection(monkeypatch):
    chat_id = 881030
    striker = TeamPlayer(user_id=10, name="AFK batter")
    session = TeamMatchSession(
        match_id="team-afk-batter-test",
        chat_id=chat_id,
        host_id=10,
        status="active",
        batting_team="team_a",
        bowling_team="team_b",
        captain_a_id=10,
        team_a_players=[
            striker,
            TeamPlayer(user_id=11, name="Non-striker"),
            TeamPlayer(user_id=12, name="Next batter"),
        ],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler")],
        striker_idx=0,
        non_striker_idx=1,
        current_bowler_id=20,
        pending_bowler_pick=3,
    )
    context = make_context()
    send_media = AsyncMock()
    monkeypatch.setattr(team.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(team, "send_event_media", send_media)
    monkeypatch.setattr(team, "_update_team_live_score", AsyncMock())
    team_match_service.active_sessions[chat_id] = session
    try:
        await team.run_team_batter_timer(context, session)
        assert striker.is_out
        assert session.wickets_a == 1
        assert session.balls_bowled_a == 1
        assert session.total_runs_a == 0
        assert session.striker_idx is None
        assert session.pending_bowler_pick is None
        assert send_media.await_args.kwargs["event_key"] == "duck"
        assert any(
            "/batting" in call.kwargs.get("text", "")
            for call in context.bot.send_message.await_args_list
        )
    finally:
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
async def test_team_owner_can_run_host_only_setup_command(monkeypatch):
    chat_id = 881003
    session = TeamMatchSession(
        match_id="team-owner-test",
        chat_id=chat_id,
        host_id=10,
        status="lobby",
        team_a_players=[TeamPlayer(user_id=10, name="Host")],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler")],
    )
    team_match_service.active_sessions[chat_id] = session
    monkeypatch.setattr(permission_service, "is_owner", lambda user_id: user_id == 99)
    context = make_context()
    context.args = ["2"]
    update = make_command_update(chat_id, 99, context.args)
    try:
        await team.set_overs_command(update, context)
        assert session.max_overs == 2
        assert session.status == "choosing_captains"
        update.message.reply_html.assert_not_awaited()
    finally:
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
async def test_team_toss_is_randomized_and_only_winning_captain_can_choose(monkeypatch):
    chat_id = 881004
    session = TeamMatchSession(
        match_id="tm-toss-test",
        chat_id=chat_id,
        host_id=10,
        status="awaiting_toss",
        captain_a_id=11,
        captain_b_id=21,
        team_a_players=[TeamPlayer(user_id=11, name="Captain A")],
        team_b_players=[TeamPlayer(user_id=21, name="Captain B")],
    )
    team_match_service.active_sessions[chat_id] = session
    monkeypatch.setattr(team.random, "choice", lambda choices: choices[0])
    context = make_context()
    toss_update = make_command_update(chat_id, 10)

    try:
        await team.toss_command(toss_update, context)
        assert session.status == "toss_call"
        assert session.toss_caller_id == 11
        assert session.sent_media_events == {"team-toss"}
        toss_data = toss_update.message.reply_html.call_args.kwargs["reply_markup"]
        assert "Heads" in str(toss_data.inline_keyboard)
        assert "Tails" in str(toss_data.inline_keyboard)

        call_update = make_callback_update(
            chat_id, 11, f"tosscall_{session.match_id}_heads"
        )
        await team.team_callback_handler(call_update, context)
        assert session.toss_call == "Heads"
        assert session.toss_result == "Heads"
        assert session.toss_winner == "team_a"
        assert session.status == "toss_decision"

        rejected = make_callback_update(
            chat_id, 21, f"tossdec_{session.match_id}_bat"
        )
        await team.team_callback_handler(rejected, context)
        rejected.callback_query.answer.assert_awaited_with(
            "⚠️ Only the toss-winning captain can choose, and only once.",
            show_alert=True,
        )
        assert session.status == "toss_decision"

        accepted = make_callback_update(
            chat_id, 11, f"tossdec_{session.match_id}_bat"
        )
        await team.team_callback_handler(accepted, context)
        assert session.status == "active"
        assert session.toss_decision == "Batting"
        assert session.batting_team == "team_a"
        accepted.callback_query.edit_message_text.assert_awaited_once()
    finally:
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
async def test_team_bowler_cannot_be_changed_mid_over_or_repeat_consecutive_over():
    chat_id = 881005
    session = TeamMatchSession(
        match_id="team-bowler-lock-test",
        chat_id=chat_id,
        host_id=10,
        status="active",
        batting_team="team_a",
        team_a_players=[TeamPlayer(user_id=1, name="Striker")],
        team_b_players=[
            TeamPlayer(user_id=31, name="Previous Bowler"),
            TeamPlayer(user_id=32, name="Next Bowler"),
        ],
        current_bowler_id=31,
        ball_in_over=1,
        captain_b_id=10,
    )
    team_match_service.active_sessions[chat_id] = session
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(team, "_start_delivery", AsyncMock())
    context = make_context()
    update = make_command_update(chat_id, 10)
    try:
        context.args = ["2"]
        await team.bowling_command(update, context)
        assert session.current_bowler_id == 31
        assert "locked until this over" in update.message.reply_html.call_args[0][0]

        session.ball_in_over = 0
        session.current_bowler_id = None
        session.previous_over_bowler_id = 31
        update.message.reply_html.reset_mock()
        context.args = ["1"]
        await team.bowling_command(update, context)
        assert session.current_bowler_id is None
        assert "previous over's bowler" in update.message.reply_html.call_args[0][0]

        context.args = ["2"]
        await team.bowling_command(update, context)
        assert session.current_bowler_id == 32

        session.team_b_players = [TeamPlayer(user_id=31, name="Previous Bowler")]
        session.current_bowler_id = None
        session.previous_over_bowler_id = 31
        context.args = []
        update.message.reply_html.reset_mock()
        await team.bowling_command(update, context)
        assert session.current_bowler_id is None
        caption = context.bot.send_photo.await_args.kwargs["caption"]
        assert context.bot.send_photo.await_args.kwargs["photo"].name == team.TEAM_BOWLING_POSTER_PATH
        assert "No eligible bowler" in caption
        assert "Current Bowler: Not assigned yet" in caption
    finally:
        monkeypatch.undo()
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
async def test_team_batter_is_locked_after_facing_a_ball_and_can_change_after_wicket(monkeypatch):
    chat_id = 881009
    session = TeamMatchSession(
        match_id="team-batter-lock-test",
        chat_id=chat_id,
        host_id=10,
        status="active",
        batting_team="team_a",
        bowling_team="team_b",
        team_a_players=[
            TeamPlayer(user_id=10, name="Striker"),
            TeamPlayer(user_id=11, name="Non-striker"),
            TeamPlayer(user_id=12, name="Next batter"),
        ],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler")],
        striker_idx=0,
        non_striker_idx=1,
        striker_faced_ball=True,
        captain_a_id=10,
    )
    team_match_service.active_sessions[chat_id] = session
    monkeypatch.setattr(permission_service, "is_owner", lambda user_id: user_id == 99)
    context = make_context()
    context.args = ["3"]
    update = make_command_update(chat_id, 99)
    try:
        await team.batting_command(update, context)
        assert session.striker_idx == 0
        assert "locked for this turn" in update.message.reply_html.call_args[0][0]

        session.striker_idx = None
        session.striker_faced_ball = False
        session.current_bowler_id = None
        update.message.reply_html.reset_mock()
        await team.batting_command(update, context)
        assert session.striker_idx == 2
        assert session.non_striker_idx == 1
    finally:
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
async def test_pinned_team_score_updates_in_place_and_ends_with_short_result():
    session = TeamMatchSession(
        match_id="team-score-test",
        chat_id=881006,
        status="active",
        live_score_message_id=701,
        team_a_name="Blue",
        team_b_name="Red",
        total_runs_a=12,
        wickets_a=1,
        balls_bowled_a=8,
        current_innings=2,
        batting_team="team_b",
        bowling_team="team_a",
        target=12,
        team_a_players=[TeamPlayer(user_id=1, name="A")],
        team_b_players=[TeamPlayer(user_id=2, name="B")],
    )
    context = make_context()

    await team._update_team_live_score(context, session)
    first_text = context.bot.edit_message_text.call_args.kwargs["text"]
    assert "Blue</b>: 12/1 (1.2 ov)" in first_text
    assert "Red</b>: 0/0 (0.0 ov)" in first_text
    assert "Target 13" in first_text
    assert "CRR" in first_text and "RRR" in first_text
    assert "Refresh" not in first_text
    assert context.bot.edit_message_text.call_args.kwargs["reply_markup"] is None

    session.status = "completed"
    session.final_result = "🏆 <b>Blue won by 12 runs!</b>"
    await team._update_team_live_score(context, session)
    final_text = context.bot.edit_message_text.call_args.kwargs["text"]
    assert "FINAL TEAM SCORE" in final_text
    assert "Blue</b>: 12/1" in final_text and "Red</b>: 0/0" in final_text
    assert "Blue won by 12 runs" in final_text
    assert context.bot.edit_message_text.await_count == 2
    context.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_pinned_score_edit_errors_do_not_send_duplicate_messages():
    session = TeamMatchSession(
        match_id="team-score-edit-error",
        chat_id=881010,
        status="active",
        live_score_message_id=702,
    )
    context = make_context()
    context.bot.edit_message_text.side_effect = BadRequest("Message can't be edited")

    await team._update_team_live_score(context, session)

    context.bot.edit_message_text.assert_awaited_once()
    context.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_team_end_requires_real_group_admin_and_allows_one_to_end():
    chat_id = 881007
    session = TeamMatchSession(
        match_id="team-admin-end-test",
        chat_id=chat_id,
        host_id=10,
        status="active",
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    update = make_command_update(chat_id, 99)
    try:
        await team.end_match_command(update, context)
        assert context.bot.get_chat_member.await_args.kwargs == {
            "chat_id": chat_id,
            "user_id": 99,
        }
        update.message.reply_html.assert_awaited_once()
        assert update.message.reply_html.call_args.kwargs["reply_markup"] is not None

        context.bot.get_chat_member.return_value = SimpleNamespace(status="member")
        unauthorized = make_command_update(chat_id, 98)
        await team.end_match_command(unauthorized, context)
        assert "host or a group administrator" in unauthorized.message.reply_html.call_args[0][0]
        assert unauthorized.message.reply_html.call_args.kwargs.get("reply_markup") is None
    finally:
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
async def test_sololineup_command_reports_the_active_lineup_or_no_match():
    chat_id = 881011
    session = MatchSession(
        match_id="solo-lineup-command-test",
        chat_id=chat_id,
        host_id=1,
        host_name="Batter",
        status="active",
        players=[
            PlayerSessionStats(user_id=1, name="Batter", has_batted=True),
            PlayerSessionStats(user_id=2, name="Bowler"),
        ],
    )
    match_service.active_sessions[session.match_id] = session
    update = make_command_update(chat_id, 1)
    context = make_context()
    try:
        await solo.sololineup_command(update, context)
        assert "SOLO LINEUP" in update.message.reply_html.call_args[0][0]
        assert "Bowling order" in update.message.reply_html.call_args[0][0]

        match_service.active_sessions.pop(session.match_id)
        update.message.reply_html.reset_mock()
        await solo.sololineup_command(update, context)
        assert "No active Solo match" in update.message.reply_html.call_args[0][0]
    finally:
        match_service.active_sessions.pop(session.match_id, None)


@pytest.mark.asyncio
async def test_solo_end_backup_preserves_bowling_progress_and_releases_group():
    session = MatchSession(
        match_id="solo-backup-progress",
        chat_id=881008,
        host_id=1,
        host_name="Host",
        status="active",
        players=[
            PlayerSessionStats(
                user_id=1,
                name="Host",
                wickets=2,
                runs_conceded=13,
                balls_bowled=9,
            ),
            PlayerSessionStats(user_id=2, name="Opponent"),
        ],
    )
    match_service.active_sessions[session.match_id] = session
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(permission_service, "is_chat_admin", AsyncMock(return_value=False))
    context = make_context()
    query = make_callback_update(
        session.chat_id, 1, f"endsolo_yes_{session.match_id}"
    ).callback_query
    update = SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=session.chat_id, type="supergroup"),
        effective_user=SimpleNamespace(id=1, first_name="Host"),
    )
    try:
        await solo.solo_callback_handler(update, context)
        backup = json.loads(context.bot.send_document.call_args.kwargs["document"].getvalue())
        assert backup["players"][0]["balls_bowled"] == 9
        assert backup["players"][0]["wickets"] == 2
        assert backup["players"][0]["runs_conceded"] == 13
        assert match_service.get_active_session_for_chat(session.chat_id) is None
    finally:
        monkeypatch.undo()
        match_service.active_sessions.pop(session.match_id, None)
