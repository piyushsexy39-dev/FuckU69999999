import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gullycric.src.engine.match_engine import MultiplayerSoloEngine, PlayerSessionStats
from gullycric.src.handlers import solo, team
from gullycric.src.services.commentary import (
    COMMENTARY_DATABASE,
    RUN_COMMENTARY_ADDITIONS,
    get_run_commentary,
)
from gullycric.src.services.match_service import MatchSession, match_service
from gullycric.src.services.media_service import MediaService
from gullycric.src.services.team_match_service import (
    TeamMatchSession,
    TeamPlayer,
    team_match_service,
)


def delivery_commentary_pool(runs):
    if runs == 0:
        return COMMENTARY_DATABASE["dot"]
    if runs == 4:
        return COMMENTARY_DATABASE["four"]
    if runs == 6:
        return COMMENTARY_DATABASE["six"]
    if runs in RUN_COMMENTARY_ADDITIONS:
        return [get_run_commentary(runs), RUN_COMMENTARY_ADDITIONS[runs]]
    return [get_run_commentary(runs)]


def make_solo_session(chat_id=62001):
    session = MatchSession(
        match_id=f"test-solo-{chat_id}",
        chat_id=chat_id,
        host_id=10,
        host_name="Batter",
        status="active",
        balls_per_turn=6,
        players=[
            PlayerSessionStats(user_id=10, name="Batter"),
            PlayerSessionStats(user_id=20, name="Bowler"),
        ],
    )
    match_service.active_sessions[session.match_id] = session
    return session


def make_update(chat_id, user_id, text, chat_type="group", first_name="Player"):
    message = MagicMock(text=text)
    message.reply_html = AsyncMock()
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=user_id, first_name=first_name, username=None),
        message=message,
    )


def make_context():
    bot = SimpleNamespace(
        send_message=AsyncMock(),
        send_video=AsyncMock(
            return_value=SimpleNamespace(video=SimpleNamespace(file_id="test-file-id"))
        ),
        send_animation=AsyncMock(),
        send_photo=AsyncMock(),
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
    )
    return SimpleNamespace(bot=bot)


@pytest.mark.asyncio
async def test_solo_bowler_callback_sends_batting_media_with_prompt(monkeypatch):
    session = make_solo_session(62030)
    context = make_context()
    send_media = AsyncMock()
    monkeypatch.setattr(solo, "send_event_media", send_media)
    monkeypatch.setattr(solo, "cancel_turn_timer", lambda _session: None)
    monkeypatch.setattr(solo, "start_batter_timer", lambda *_args: None)
    query = SimpleNamespace(
        data=f"bowl_{session.match_id}_3",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=20),
        effective_chat=SimpleNamespace(id=session.chat_id, type="private"),
    )
    try:
        await solo.solo_callback_handler(update, context)

        send_media.assert_awaited_once()
        media_call = send_media.await_args
        assert media_call.args[2] == solo.media_service.get_local_media_path("batting")
        assert media_call.kwargs["event_key"] == "batting"
        assert "Batter" in media_call.args[3]
        assert "SR 0.0" in media_call.args[3]
        assert "Reply" in media_call.args[3] and "1-6" in media_call.args[3]
    finally:
        match_service.active_sessions.pop(session.match_id, None)


@pytest.mark.asyncio
async def test_team_bowler_callback_sends_batting_media_with_prompt(monkeypatch):
    chat_id = 62031
    session = TeamMatchSession(
        match_id="team-batting-prompt",
        chat_id=chat_id,
        status="active",
        batting_team="team_a",
        bowling_team="team_b",
        team_a_players=[TeamPlayer(user_id=10, name="Batter", runs=9, balls=6)],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler")],
        striker_idx=0,
        current_bowler_id=20,
    )
    team_match_service.active_sessions[chat_id] = session
    context = make_context()
    send_media = AsyncMock()
    monkeypatch.setattr(team, "send_event_media", send_media)
    monkeypatch.setattr(team, "cancel_team_turn_timer", lambda _session: None)
    monkeypatch.setattr(team, "start_team_batter_timer", lambda *_args: None)
    query = SimpleNamespace(
        data=f"tbowl_{session.match_id}_4",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=20),
        effective_chat=SimpleNamespace(id=chat_id, type="private"),
    )
    try:
        await team.team_callback_handler(update, context)

        send_media.assert_awaited_once()
        media_call = send_media.await_args
        assert media_call.args[2] == solo.media_service.get_local_media_path("batting")
        assert media_call.kwargs["event_key"] == "batting"
        assert "Batter to bat · 9r (6b)" in media_call.args[3]
        assert "SR 150.0" in media_call.args[3]
        assert "Reply" in media_call.args[3] and "1-6" in media_call.args[3]
    finally:
        team_match_service.remove_session(chat_id)


@pytest.mark.asyncio
async def test_solo_rejects_zero_for_batter_and_bowler(monkeypatch):
    session = make_solo_session()
    try:
        context = make_context()
        bowler_input = make_update(session.chat_id, 20, "0", "private")
        await solo.handle_chat_text_input(bowler_input, context)
        assert "bowler must choose a number from 1 to 6" in bowler_input.message.reply_html.call_args[0][0]
        assert session.pending_bowler_pick is None

        batter_input = make_update(session.chat_id, 10, "0")
        await solo.handle_chat_text_input(batter_input, context)
        assert "Solo batter must choose a number from 1 to 6" in batter_input.message.reply_html.call_args[0][0]
        assert session.players[0].runs == 0

        bowler_result = await match_service.record_delivery_interaction(
            session.match_id, 20, 0, "bowler"
        )
        batter_result = await match_service.record_delivery_interaction(
            session.match_id, 10, 0, "batter"
        )
        assert bowler_result["status"] == "error"
        assert batter_result["status"] == "error"
        assert session.pending_bowler_pick is None
        assert session.players[0].runs == 0

        team_session = TeamMatchSession(
            match_id="test-team-text-zero",
            chat_id=62009,
            status="active",
            team_a_players=[TeamPlayer(user_id=11, name="Team batter")],
            team_b_players=[TeamPlayer(user_id=21, name="Team bowler")],
            current_bowler_id=21,
        )
        team_match_service.active_sessions[team_session.chat_id] = team_session
        team_bowler_input = make_update(team_session.chat_id, 21, "0", "private")
        await solo.handle_chat_text_input(team_bowler_input, context)
        assert "bowler must choose a number from 1 to 6" in team_bowler_input.message.reply_html.call_args[0][0]
        assert team_session.pending_bowler_pick is None
        team_match_service.remove_session(team_session.chat_id)
    finally:
        match_service.active_sessions.pop(session.match_id, None)


@pytest.mark.asyncio
async def test_solo_join_only_sends_individual_confirmation(monkeypatch):
    session = MatchSession(
        match_id="test-solo-join",
        chat_id=62020,
        host_id=10,
        host_name="Host",
        status="lobby",
        players=[PlayerSessionStats(user_id=10, name="Host")],
        countdown_task=object(),
    )
    match_service.active_sessions[session.match_id] = session
    monkeypatch.setattr(
        match_service, "join_solo_lobby", AsyncMock(return_value=session)
    )
    update = make_update(session.chat_id, 20, "/joinsolo", first_name="Floral")
    try:
        await solo.join_command(update, make_context())
        update.message.reply_html.assert_awaited_once_with(
            "✅ <b>Floral</b> joined the solo queue!"
        )
    finally:
        match_service.active_sessions.pop(session.match_id, None)


@pytest.mark.parametrize(
    ("mode", "milestone"),
    [("solo", 50), ("solo", 100), ("team", 50), ("team", 100)],
)
@pytest.mark.asyncio
async def test_run_milestone_celebration_uses_tagged_player_and_video(
    monkeypatch, mode, milestone
):
    events = []
    context = make_context()
    context.bot.send_video.side_effect = lambda **kwargs: events.append(
        ("milestone", kwargs)
    )
    context.bot.send_message.side_effect = lambda **kwargs: events.append(
        ("message", kwargs)
    )

    if mode == "solo":
        session = make_solo_session(62023 + milestone)
        batter = session.players[0]
        batter.runs = milestone - 1
        monkeypatch.setattr(
            solo,
            "send_event_media",
            AsyncMock(side_effect=lambda *args, **kwargs: events.append(("gif", kwargs))),
        )
        monkeypatch.setattr(solo, "cancel_turn_timer", lambda _session: None)
        monkeypatch.setattr(solo, "start_batter_timer", lambda *_args: None)
        monkeypatch.setattr(solo, "start_bowler_timer", lambda *_args: None)

        await solo.handle_chat_text_input(
            make_update(session.chat_id, 20, "2", "private"), context
        )
        await solo.handle_chat_text_input(
            make_update(session.chat_id, 10, "1"), context
        )
        celebrated_player = batter
        expected_key = (1, batter.user_id, milestone)
    else:
        session = TeamMatchSession(
            match_id=f"test-team-milestone-{milestone}",
            chat_id=62024 + milestone,
            status="active",
            max_overs=2,
            balls_per_over=6,
            team_a_players=[
                TeamPlayer(user_id=31, name="Team batter", runs=milestone - 1)
            ],
            team_b_players=[TeamPlayer(user_id=41, name="Team bowler")],
            batting_team="team_a",
            bowling_team="team_b",
            striker_idx=0,
            current_bowler_id=41,
            pending_bowler_pick=6,
        )
        team_match_service.active_sessions[session.chat_id] = session
        monkeypatch.setattr(
            team,
            "send_event_media",
            AsyncMock(side_effect=lambda *args, **kwargs: events.append(("gif", kwargs))),
        )
        monkeypatch.setattr(team, "_update_team_live_score", AsyncMock())
        monkeypatch.setattr(team, "_start_delivery", AsyncMock())
        await team.handle_team_chat_input(
            make_update(session.chat_id, 31, "1"), context
        )
        celebrated_player = session.team_a_players[0]
        expected_key = (1, 31, milestone)

    try:
        await solo.send_run_milestone_celebrations(
            context,
            session.chat_id,
            celebrated_player,
            milestone - 1,
            1,
            session.celebrated_milestones,
        )
        milestone_messages = [kwargs for kind, kwargs in events if kind == "milestone"]
        assert len(milestone_messages) == 1
        celebration = milestone_messages[0]
        assert celebration["video"] == solo.MILESTONE_VIDEO_URL
        assert f"tg://user?id={celebrated_player.user_id}" in celebration["caption"]
        assert f"{milestone} RUNS COMPLETED!" in celebration["caption"]
        assert expected_key in session.celebrated_milestones
        outcome_index = next(
            index
            for index, (kind, kwargs) in enumerate(events)
            if kind == "gif" and kwargs.get("event_key") == "shot_1"
        )
        assert events[outcome_index + 1][0] == "milestone"
        assert events[outcome_index + 2][0] == "message"
    finally:
        if mode == "solo":
            match_service.active_sessions.pop(session.match_id, None)
        else:
            team_match_service.remove_session(session.chat_id)


@pytest.mark.parametrize(
    ("mode", "prompt", "expected_user_id", "other_user_id"),
    [
        ("solo", "bowling", 20, 10),
        ("solo", "batting", 10, 20),
        ("team", "bowling", 41, 31),
        ("team", "batting", 31, 41),
    ],
)
@pytest.mark.asyncio
async def test_countdown_prompts_only_mention_the_active_player(
    monkeypatch, mode, prompt, expected_user_id, other_user_id
):
    context = make_context()
    if mode == "solo":
        session = make_solo_session(62021)
        timer = (
            solo.run_bowler_turn_timer
            if prompt == "bowling"
            else solo.run_batter_turn_timer
        )
        timer_module = solo
    else:
        session = TeamMatchSession(
            match_id=f"test-team-{prompt}-timer",
            chat_id=62022,
            status="active",
            team_a_players=[TeamPlayer(user_id=31, name="Team batter")],
            team_b_players=[TeamPlayer(user_id=41, name="Team bowler")],
            striker_idx=0,
            current_bowler_id=41,
            pending_bowler_pick=2 if prompt == "batting" else None,
        )
        timer = (
            team.run_team_bowler_timer
            if prompt == "bowling"
            else team.run_team_batter_timer
        )
        timer_module = team

    monkeypatch.setattr(
        timer_module.asyncio,
        "sleep",
        AsyncMock(side_effect=[None, None, asyncio.CancelledError()]),
    )
    if mode == "team":
        team_match_service.active_sessions[session.chat_id] = session
    try:
        await timer(context, session)
        texts = [call.kwargs["text"] for call in context.bot.send_message.await_args_list]
        assert len(texts) == 2
        all_text = "\n".join(texts)
        assert f"tg://user?id={expected_user_id}" in all_text
        assert f"tg://user?id={other_user_id}" not in all_text
    finally:
        if mode == "solo":
            match_service.active_sessions.pop(session.match_id, None)
        else:
            team_match_service.remove_session(session.chat_id)


@pytest.mark.parametrize(
    ("runs", "expected_event"),
    [(1, "shot_1"), (3, "single"), (5, "shot_5")],
)
@pytest.mark.asyncio
async def test_solo_delivery_scores_and_uses_exact_run_commentary(
    monkeypatch, runs, expected_event
):
    session = make_solo_session(62002)
    try:
        context = make_context()
        monkeypatch.setattr(solo, "send_event_media", AsyncMock())
        monkeypatch.setattr(solo, "cancel_turn_timer", lambda _session: None)
        monkeypatch.setattr(solo, "start_batter_timer", lambda *_args: None)
        monkeypatch.setattr(solo, "start_bowler_timer", lambda *_args: None)

        await solo.handle_chat_text_input(
            make_update(session.chat_id, 20, "2", "private"), context
        )
        shot_update = make_update(session.chat_id, 10, str(runs))
        shot_update.message.message_id = 7341
        await solo.handle_chat_text_input(shot_update, context)

        assert session.players[0].runs == runs
        assert session.players[1].runs_conceded == runs
        media_call = next(
            call
            for call in solo.send_event_media.await_args_list
            if call.kwargs.get("event_key") == expected_event
        )
        caption = media_call.kwargs.get("caption") or media_call.args[3]
        assert all(line not in caption for line in delivery_commentary_pool(runs))
        assert media_call.kwargs["reply_to_message_id"] == 7341
        assert sum(
            call.kwargs.get("text") in delivery_commentary_pool(runs)
            for call in context.bot.send_message.await_args_list
        ) == 1
        assert all(
            "Hindi commentary:" not in call.kwargs.get("text", "")
            for call in context.bot.send_message.await_args_list
        )
    finally:
        match_service.active_sessions.pop(session.match_id, None)


@pytest.mark.asyncio
async def test_solo_wicket_commentary_does_not_change_batter_runs(monkeypatch):
    session = make_solo_session(62010)
    session.unlimited_overs = True
    session.players.append(PlayerSessionStats(user_id=30, name="Next batter"))
    try:
        context = make_context()
        media_sender = AsyncMock()
        monkeypatch.setattr(solo, "send_event_media", media_sender)
        monkeypatch.setattr(solo, "cancel_turn_timer", lambda _session: None)
        monkeypatch.setattr(solo, "start_batter_timer", lambda *_args: None)
        monkeypatch.setattr(solo, "start_bowler_timer", lambda *_args: None)

        await solo.handle_chat_text_input(
            make_update(session.chat_id, 20, "2", "private"), context
        )
        shot_update = make_update(session.chat_id, 10, "2")
        shot_update.message.message_id = 7342
        await solo.handle_chat_text_input(shot_update, context)

        assert session.players[0].runs == 0
        assert session.players[0].is_out
        assert session.players[1].wickets == 1
        wicket_caption = next(
            call.kwargs.get("caption") or call.args[3]
            for call in media_sender.await_args_list
            if call.kwargs.get("event_key") in ("duck", "wicket")
        )
        assert "💬" not in wicket_caption
        assert next(
            call.kwargs["reply_to_message_id"]
            for call in media_sender.await_args_list
            if call.kwargs.get("event_key") in ("duck", "wicket")
        ) == 7342
        assert sum(
            call.kwargs.get("text") == hindi
            for call in context.bot.send_message.await_args_list
            for hindi in COMMENTARY_DATABASE["duck"]
        ) == 1
        assert any(
            call.kwargs.get("event_key") in ("duck", "wicket")
            for call in media_sender.await_args_list
        )
    finally:
        match_service.active_sessions.pop(session.match_id, None)


@pytest.mark.asyncio
async def test_team_bowler_callback_rejects_zero():
    session = TeamMatchSession(
        match_id="test-team-zero",
        chat_id=62003,
        status="active",
        team_a_players=[TeamPlayer(user_id=10, name="Batter")],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler")],
        current_bowler_id=20,
    )
    team_match_service.active_sessions[session.chat_id] = session
    try:
        query = SimpleNamespace(
            data=f"tbowl_{session.match_id}_0",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=20),
            effective_chat=SimpleNamespace(id=session.chat_id),
        )
        await team.team_callback_handler(update, make_context())
        assert session.pending_bowler_pick is None
        assert any(
            call.kwargs.get("show_alert")
            and "bowler must choose a number from 1 to 6" in call.args[0]
            for call in query.answer.await_args_list
        )
    finally:
        team_match_service.remove_session(session.chat_id)


@pytest.mark.parametrize(
    ("runs", "expected_event"),
    [(0, "dot"), (1, "shot_1"), (5, "shot_5")],
)
@pytest.mark.asyncio
async def test_team_batter_uses_original_and_requested_animations(
    monkeypatch, runs, expected_event
):
    session = TeamMatchSession(
        match_id="test-team-dot",
        chat_id=62004,
        status="active",
        max_overs=2,
        balls_per_over=6,
        team_a_players=[
            TeamPlayer(user_id=10, name="Striker"),
            TeamPlayer(user_id=11, name="NonStriker"),
            TeamPlayer(user_id=12, name="Next batter"),
        ],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler")],
        batting_team="team_a",
        bowling_team="team_b",
        striker_idx=0,
        non_striker_idx=1,
        current_bowler_id=20,
        pending_bowler_pick=1 if runs == 0 else 6,
        ball_in_over=5,
        balls_bowled_a=5,
        total_runs_a=12,
    )
    team_match_service.active_sessions[session.chat_id] = session
    try:
        media_sender = AsyncMock()
        context = make_context()
        monkeypatch.setattr(team, "send_event_media", media_sender)
        update = make_update(session.chat_id, 10, str(runs))
        update.message.message_id = 8351
        await team.handle_team_chat_input(update, context)

        striker = session.team_a_players[0]
        bowler = session.team_b_players[0]
        assert striker.runs == runs
        assert striker.balls == 1
        assert session.total_runs_a == 12 + runs
        assert session.wickets_a == 0
        assert bowler.wickets == 0
        assert any(
            call.kwargs.get("event_key") == expected_event
            and "Bowler:" not in (call.kwargs.get("caption") or call.args[3])
            for call in media_sender.await_args_list
        )
        outcome_caption = next(
            call.kwargs.get("caption") or call.args[3]
            for call in media_sender.await_args_list
            if call.kwargs.get("event_key") == expected_event
        )
        outcome_media_call = next(
            call
            for call in media_sender.await_args_list
            if call.kwargs.get("event_key") == expected_event
        )
        assert outcome_media_call.kwargs["reply_to_message_id"] == 8351
        if runs:
            assert all(
                line not in outcome_caption
                for line in delivery_commentary_pool(runs)
            )
        if runs == 0:
            assert sum(
                call.kwargs.get("text") == hindi
                for call in context.bot.send_message.await_args_list
                for hindi in COMMENTARY_DATABASE["dot"]
            ) == 1
        else:
            assert sum(
                call.kwargs.get("text") in delivery_commentary_pool(runs)
                for call in context.bot.send_message.await_args_list
            ) == 1
        assert "💬" not in outcome_caption
    finally:
        team_match_service.remove_session(session.chat_id)


@pytest.mark.asyncio
async def test_team_wicket_commentary_does_not_change_runs(monkeypatch):
    session = TeamMatchSession(
        match_id="test-team-wicket",
        chat_id=62005,
        status="active",
        max_overs=2,
        balls_per_over=6,
        team_a_players=[
            TeamPlayer(user_id=10, name="Striker", runs=7, balls=4),
            TeamPlayer(user_id=11, name="NonStriker"),
            TeamPlayer(user_id=12, name="Next batter"),
        ],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler")],
        batting_team="team_a",
        bowling_team="team_b",
        striker_idx=0,
        non_striker_idx=1,
        current_bowler_id=20,
        pending_bowler_pick=3,
        ball_in_over=5,
        balls_bowled_a=5,
        total_runs_a=12,
    )
    team_match_service.active_sessions[session.chat_id] = session
    try:
        media_sender = AsyncMock()
        context = make_context()
        monkeypatch.setattr(team, "send_event_media", media_sender)
        shot_update = make_update(session.chat_id, 10, "3")
        shot_update.message.message_id = 8352
        await team.handle_team_chat_input(shot_update, context)

        assert session.total_runs_a == 12
        assert session.wickets_a == 1
        assert session.team_a_players[0].runs == 7
        assert session.team_b_players[0].wickets == 1
        wicket_caption = next(
            call.kwargs.get("caption") or call.args[3]
            for call in media_sender.await_args_list
            if call.kwargs.get("event_key") == "wicket"
        )
        assert "💬" not in wicket_caption
        assert next(
            call.kwargs["reply_to_message_id"]
            for call in media_sender.await_args_list
            if call.kwargs.get("event_key") == "wicket"
        ) == 8352
        assert sum(
            call.kwargs.get("text") == hindi
            for call in context.bot.send_message.await_args_list
            for hindi in COMMENTARY_DATABASE["wicket"]
        ) == 1
        assert all(
            "Hindi commentary:" not in call.kwargs.get("text", "")
            for call in context.bot.send_message.await_args_list
        )
        assert any(
            call.kwargs.get("event_key") == "wicket"
            for call in media_sender.await_args_list
        )
    finally:
        team_match_service.remove_session(session.chat_id)


@pytest.mark.asyncio
async def test_team_toss_selects_a_caller_for_heads_or_tails(monkeypatch):
    session = TeamMatchSession(
        match_id="test-team-toss",
        chat_id=62006,
        host_id=10,
        status="awaiting_toss",
        team_a_name="Blue",
        team_b_name="Red",
        team_a_players=[TeamPlayer(user_id=10, name="Captain A")],
        team_b_players=[TeamPlayer(user_id=20, name="Captain B")],
        captain_a_id=10,
        captain_b_id=20,
    )
    team_match_service.active_sessions[session.chat_id] = session
    try:
        monkeypatch.setattr(team.random, "choice", lambda choices: choices[0])
        message = SimpleNamespace(reply_html=AsyncMock())
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=session.chat_id),
            effective_user=SimpleNamespace(id=10),
            message=message,
        )
        await team.toss_command(update, make_context())
        message.reply_html.assert_awaited_once()
        assert "COIN TOSS" in message.reply_html.await_args.args[0]
        keyboard = message.reply_html.await_args.kwargs["reply_markup"]
        assert keyboard is not None
        assert "Heads" in str(keyboard.inline_keyboard)
        assert "Tails" in str(keyboard.inline_keyboard)
        assert session.status == "toss_call"
        assert session.toss_caller_id == session.captain_a_id
    finally:
        team_match_service.remove_session(session.chat_id)


@pytest.mark.asyncio
async def test_original_solo_animation_assets_are_used_for_shot_events(monkeypatch):
    media_service = MediaService()
    media_service.enabled = True
    event_names = ["dot", "single", "double", "four", "six", "shot_1", "shot_5"]
    paths = {event: media_service.get_local_media_path(event) for event in event_names}
    assert all(paths[event] and paths[event].endswith(".mp4") for event in event_names[:5])
    assert paths["shot_1"] and paths["shot_1"].endswith("shot_1.mp4")
    assert paths["shot_5"] and paths["shot_5"].endswith("shot_5.mp4")

    get_cached_file_id = MagicMock(return_value=None)
    monkeypatch.setattr(solo.media_cache, "get", get_cached_file_id)
    monkeypatch.setattr(solo.media_cache, "set", MagicMock())
    context = make_context()
    context.bot.send_animation.return_value = SimpleNamespace(
        animation=SimpleNamespace(file_id="test-animation-file-id")
    )
    await solo.send_event_media(
        context, 62007, paths["dot"], "dot ball", event_key="dot"
    )
    context.bot.send_animation.assert_awaited_once()
    context.bot.send_video.assert_not_awaited()
    get_cached_file_id.assert_called_once_with("dot")


@pytest.mark.parametrize("event_key", ["shot_1", "shot_5"])
@pytest.mark.asyncio
async def test_one_and_five_run_clips_are_sent_as_videos(monkeypatch, event_key):
    media_service = MediaService()
    media_service.enabled = True
    media_path = media_service.get_local_media_path(event_key)
    assert media_path and media_path.endswith(f"{event_key}.mp4")

    monkeypatch.setattr(solo.media_cache, "get", lambda _key: None)
    monkeypatch.setattr(solo.media_cache, "set", MagicMock())
    context = make_context()

    await solo.send_event_media(
        context,
        62012,
        media_path,
        "shot outcome",
        event_key=event_key,
        reply_to_message_id=9341,
    )

    context.bot.send_video.assert_awaited_once()
    assert context.bot.send_video.await_args.kwargs["reply_to_message_id"] == 9341
    context.bot.send_animation.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_animation_upload_failure_falls_back_to_visible_document(monkeypatch):
    media_service = MediaService()
    media_service.enabled = True
    path = media_service.get_local_media_path("dot")
    assert path

    monkeypatch.setattr(solo.media_cache, "get", lambda _key: None)
    monkeypatch.setattr(solo.media_cache, "set", MagicMock())
    context = make_context()
    context.bot.send_animation.side_effect = RuntimeError("Telegram rejected animation encoding")
    context.bot.send_video.side_effect = RuntimeError("Telegram rejected video encoding")
    context.bot.send_document = AsyncMock()

    await solo.send_event_media(
        context, 62011, path, "test shot", event_key="dot"
    )

    context.bot.send_animation.assert_awaited_once()
    context.bot.send_video.assert_awaited_once()
    context.bot.send_document.assert_awaited_once()
    context.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_team_score_command_sends_detailed_scorecard_with_poster(monkeypatch):
    session = TeamMatchSession(
        match_id="test-live-score",
        chat_id=62012,
        status="active",
        max_overs=2,
        balls_per_over=6,
        team_a_name="Blue",
        team_b_name="Red",
        team_a_players=[
            TeamPlayer(user_id=10, name="Striker", runs=12, balls=7, fours=2),
            TeamPlayer(user_id=11, name="Yet to bat"),
            TeamPlayer(user_id=12, name="Out", runs=3, balls=2, is_out=True),
        ],
        team_b_players=[TeamPlayer(
            user_id=20, name="Bowler", wickets=1, runs_conceded=8, balls_bowled=6
        )],
        batting_team="team_a",
        bowling_team="team_b",
        current_innings=2,
        target=19,
        total_runs_a=12,
        wickets_a=1,
        balls_bowled_a=7,
        total_runs_b=19,
        wickets_b=1,
        balls_bowled_b=12,
        striker_idx=0,
        current_bowler_id=20,
    )
    team_match_service.active_sessions[session.chat_id] = session
    try:
        reply_photo = AsyncMock()
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=session.chat_id),
            message=SimpleNamespace(reply_photo=reply_photo),
        )
        context = make_context()
        await team.score_command(update, context)
        reply_photo.assert_awaited_once()
        caption = reply_photo.await_args.kwargs["caption"]
        assert "LIVE TEAM SCORECARD" in caption
        assert "Innings 2" in caption and "Target 20" in caption
        assert "Striker" in caption and "Bowler" in caption
        assert "yet to bat" in caption and "OUT" in caption
        assert "bowled 1.0 ov" in caption
        assert "Runs required 8" in caption and "Balls remaining 5" in caption
    finally:
        team_match_service.remove_session(session.chat_id)


@pytest.mark.asyncio
async def test_pinned_team_score_updates_same_message_without_refresh_button(monkeypatch):
    session = TeamMatchSession(
        match_id="test-pinned-score",
        chat_id=62013,
        status="active",
        team_a_name="Blue",
        team_b_name="Red",
        team_a_players=[TeamPlayer(user_id=10, name="Batter")],
        team_b_players=[TeamPlayer(user_id=20, name="Bowler")],
    )
    team_match_service.active_sessions[session.chat_id] = session
    try:
        context = make_context()
        context.bot.send_message.return_value = SimpleNamespace(message_id=91)
        context.bot.pin_chat_message = AsyncMock()
        context.bot.pin_chat_message.side_effect = RuntimeError("insufficient rights")
        context.bot.edit_message_text = AsyncMock()

        await team._update_team_live_score(context, session)
        assert session.live_score_message_id == 91
        context.bot.pin_chat_message.assert_awaited_once()
        context.bot.send_message.assert_awaited_once()
        assert context.bot.send_message.await_args.kwargs["reply_markup"] is None

        session.total_runs_a = 4
        await team._update_team_live_score(context, session)
        context.bot.send_message.assert_awaited_once()
        context.bot.edit_message_text.assert_awaited_once()
        assert "Blue</b>: 4/0" in context.bot.edit_message_text.await_args.kwargs["text"]
        assert "Refresh" not in context.bot.edit_message_text.await_args.kwargs["text"]
        assert context.bot.edit_message_text.await_args.kwargs["reply_markup"] is None
    finally:
        team_match_service.remove_session(session.chat_id)


@pytest.mark.asyncio
async def test_team_chase_over_update_reports_actual_requirements():
    session = TeamMatchSession(
        match_id="test-chase-update",
        chat_id=62014,
        status="active",
        max_overs=2,
        balls_per_over=6,
        team_a_name="Blue",
        team_b_name="Red",
        team_a_players=[TeamPlayer(user_id=10, name="Bowler")],
        team_b_players=[
            TeamPlayer(user_id=20, name="Striker"),
            TeamPlayer(user_id=21, name="Out", is_out=True),
            TeamPlayer(user_id=22, name="Next"),
            TeamPlayer(user_id=23, name="Next 2"),
        ],
        batting_team="team_b",
        bowling_team="team_a",
        current_innings=2,
        target=20,
        total_runs_b=13,
        wickets_b=1,
        balls_bowled_b=10,
    )
    context = make_context()
    await team._send_team_chase_over_update(context, session)
    text = context.bot.send_message.await_args.kwargs["text"]
    assert "Red: <b>13/1</b>" in text
    assert "Target: <b>21</b>" in text
    assert "Runs required: <b>8</b>" in text
    assert "Balls remaining: <b>2</b>" in text
    assert "Wickets remaining: <b>2</b>" in text


@pytest.mark.asyncio
async def test_team_second_innings_completed_over_posts_chase_update(monkeypatch):
    session = TeamMatchSession(
        match_id="test-chase-over-handler",
        chat_id=62015,
        status="active",
        max_overs=2,
        balls_per_over=6,
        team_a_name="Blue",
        team_b_name="Red",
        team_a_players=[TeamPlayer(user_id=30, name="Bowler")],
        team_b_players=[
            TeamPlayer(user_id=10, name="Striker"),
            TeamPlayer(user_id=11, name="NonStriker"),
            TeamPlayer(user_id=12, name="Next batter"),
        ],
        batting_team="team_b",
        bowling_team="team_a",
        current_innings=2,
        target=10,
        striker_idx=0,
        non_striker_idx=1,
        current_bowler_id=30,
        pending_bowler_pick=6,
        ball_in_over=5,
        total_runs_b=3,
        balls_bowled_b=5,
    )
    team_match_service.active_sessions[session.chat_id] = session
    try:
        monkeypatch.setattr(team, "send_event_media", AsyncMock())
        context = make_context()
        context.bot.send_message.return_value = SimpleNamespace(message_id=92)
        context.bot.pin_chat_message = AsyncMock()
        context.bot.edit_message_text = AsyncMock()
        await team.handle_team_chat_input(
            make_update(session.chat_id, 10, "1"), context
        )
        sent_texts = [
            call.kwargs["text"]
            for call in context.bot.send_message.await_args_list
            if "OVER COMPLETE · CHASE UPDATE" in call.kwargs.get("text", "")
        ]
        assert len(sent_texts) == 1
        assert "Runs required: <b>7</b>" in sent_texts[0]
        assert "Balls remaining: <b>6</b>" in sent_texts[0]
        assert session.total_runs_b == 4
        assert session.balls_bowled_b == 6
    finally:
        team_match_service.remove_session(session.chat_id)


@pytest.mark.asyncio
async def test_team_final_scoreboard_precedes_unchanged_potm(monkeypatch):
    session = TeamMatchSession(
        match_id="test-team-final",
        chat_id=62008,
        status="active",
        balls_per_over=6,
        team_a_name="Blue",
        team_b_name="Red",
        total_runs_a=28,
        wickets_a=1,
        balls_bowled_a=12,
        total_runs_b=20,
        wickets_b=2,
        balls_bowled_b=12,
        team_a_players=[
            TeamPlayer(user_id=10, name="Top batter", runs=20, balls=8, fours=2, sixes=1),
            TeamPlayer(user_id=11, name="Bowling allrounder", wickets=1, runs_conceded=10, balls_bowled=6),
        ],
        team_b_players=[
            TeamPlayer(user_id=20, name="Best bowler", runs=4, balls=3, wickets=2, runs_conceded=8, balls_bowled=6),
        ],
    )
    team_match_service.active_sessions[session.chat_id] = session
    events = []

    async def send_message(**kwargs):
        events.append(("scoreboard", kwargs["text"]))

    async def send_potm(*_args, **_kwargs):
        events.append(("potm", ""))

    monkeypatch.setattr(team.media_service, "get_local_media_path", lambda _event: None)
    monkeypatch.setattr(solo, "send_potm_with_pfp", send_potm)
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(side_effect=send_message)))
    try:
        await team._conclude_team_match(
            context, session, "🎉 <b>Winner: Blue won by 8 runs!</b>"
        )
        assert [event[0] for event in events] == ["scoreboard", "potm"]
        scoreboard = events[0][1]
        for expected in (
            "Blue</b>: 28/1",
            "Red</b>: 20/2",
            "won by 8 runs",
            "Top batter",
            "20 runs (8b)",
            "Best bowler",
            "2/8",
            "Bowling allrounder",
        ):
            assert expected in scoreboard
    finally:
        team_match_service.remove_session(session.chat_id)


def test_all_six_run_commentaries_are_exact():
    expected = {
        1: "1️⃣ Ek run se shuruaat, jeet ki taraf pehla kadam! 💪",
        2: "2️⃣ Do run ki raftaar, player hai dumdaar! 🔥",
        3: "3️⃣ Teen run ki kamaal chaal, batter ne macha diya dhamaal! 😎",
        4: "4️⃣ CHAUUKAAA! Boundary ke paar, batter ka zabardast vaar! 💥",
        5: "5️⃣ Paanch run ki maar, opposition bhi bole — kya khiladi hai yaar! 😭🔥",
        6: "6️⃣ CHHAKKAAA! Gend gayi aasmaan ke paar, batter hai aaj form mein shandaar! 🚀🏏",
    }
    assert {runs: get_run_commentary(runs) for runs in range(1, 7)} == expected


def test_solo_engine_rejects_zero_before_changing_stats():
    engine = MultiplayerSoloEngine()
    batter = PlayerSessionStats(user_id=10, name="Batter")
    bowler = PlayerSessionStats(user_id=20, name="Bowler")

    with pytest.raises(ValueError, match="Solo batter must choose"):
        engine.process_delivery(batter, bowler, 0, 1, 1)
    with pytest.raises(ValueError, match="bowler must choose"):
        engine.process_delivery(batter, bowler, 1, 0, 1)
    assert batter.balls == 0
    assert bowler.balls_bowled == 0
