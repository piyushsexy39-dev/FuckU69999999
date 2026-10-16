import time
import json
import asyncio
import random
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from sqlalchemy import select

from ..config import config
from ..db.session import async_session_factory
from ..db.models import Match as MatchModel, User
from ..engine.match_engine import (
    MultiplayerSoloEngine,
    PlayerSessionStats,
    BallResult
)
from ..services.economy import economy_service

@dataclass
class MatchSession:
    match_id: str
    chat_id: int
    host_id: int
    host_name: str
    mode: str = "solo"
    status: str = "lobby"           # lobby, countdown, active, completed, cancelled
    balls_per_turn: int = 6         # 3 or 6 balls per turn
    
    # All players in queue (Host is #0)
    players: List[PlayerSessionStats] = field(default_factory=list)
    
    # Rotation state
    current_batter_idx: int = 0
    current_bowler_idx: int = 1
    current_turn_ball: int = 0      # 1 to balls_per_turn
    current_over_balls: List[Dict[str, Any]] = field(default_factory=list)
    
    # Pending picks for active delivery
    pending_bowler_pick: Optional[int] = None
    pending_batter_pick: Optional[int] = None
    
    # Lobby countdown
    countdown_seconds: int = 50
    # Live lobby message (edited in real time, pinned, then turned into 'match started')
    lobby_message_id: Optional[int] = None
    lobby_has_media: bool = False
    lobby_pinned: bool = False
    countdown_task: Optional[asyncio.Task] = None
    turn_timer_task: Optional[asyncio.Task] = None
    unlimited_overs: bool = False
    spamfree: bool = False
    spamfree_last_bowler_id: Optional[int] = None
    spamfree_last_bowler_pick: Optional[int] = None
    spamfree_consecutive_count: int = 0
    
    # Midgame join requests (user_id -> user_name)
    pending_join_requests: Dict[int, str] = field(default_factory=dict)
    celebrated_milestones: set[tuple[int, int, int]] = field(default_factory=set)
    sent_media_events: set[str] = field(default_factory=set)

    winner_id: Optional[int] = None
    winner_name: Optional[str] = None
    result_summary: Optional[str] = None
    version: int = 1
    last_action_timestamp: float = field(default_factory=time.time)

    @property
    def current_batter(self) -> Optional[PlayerSessionStats]:
        if 0 <= self.current_batter_idx < len(self.players):
            return self.players[self.current_batter_idx]
        return None

    @property
    def current_bowler(self) -> Optional[PlayerSessionStats]:
        if 0 <= self.current_bowler_idx < len(self.players):
            return self.players[self.current_bowler_idx]
        return None

class MatchService:
    def __init__(self):
        self.active_sessions: Dict[str, MatchSession] = {}
        self.engine = MultiplayerSoloEngine()

    def get_session(self, match_id: str) -> Optional[MatchSession]:
        return self.active_sessions.get(match_id)

    def get_active_session_for_chat(self, chat_id: int) -> Optional[MatchSession]:
        for s in self.active_sessions.values():
            if s.chat_id == chat_id and s.status in ("lobby", "countdown", "active"):
                return s
        return None

    async def create_solo_lobby(
        self,
        chat_id: int,
        host_id: int,
        host_name: str,
        balls_per_turn: int = 6,
        unlimited_overs: bool = False
    ) -> Optional[MatchSession]:
        if self.get_active_session_for_chat(chat_id):
            return None
        from .team_match_service import team_match_service
        team_session = team_match_service.get_session(chat_id)
        if team_session and team_session.status in (
            "lobby", "awaiting_overs", "choosing_captains", "awaiting_toss",
            "toss_call", "toss_decision", "active",
        ):
            return None

        match_id = f"m{int(time.time())}{random.randint(100, 999)}"
        host_player = self.engine.create_player(host_id, host_name)
        
        session = MatchSession(
            match_id=match_id,
            chat_id=chat_id,
            host_id=host_id,
            host_name=host_name,
            mode="solo",
            status="lobby",
            balls_per_turn=balls_per_turn,
            players=[host_player],
            countdown_seconds=50,
            unlimited_overs=unlimited_overs
        )
        self.active_sessions[match_id] = session

        # Persist to database
        async with async_session_factory() as db:
            db_match = MatchModel(
                match_id=match_id,
                chat_id=chat_id,
                host_id=host_id,
                mode="solo",
                status="lobby",
                balls_per_over=balls_per_turn,
                max_overs=1,
                team_a_players=json.dumps([host_id]),
                team_b_players=json.dumps([]),
                version=1
            )
            db.add(db_match)
            await db.commit()

        return session

    async def join_solo_lobby(self, match_id: str, user_id: int, user_name: str) -> Optional[MatchSession]:
        session = self.get_session(match_id)
        if not session:
            return None

        # If already in match
        if any(p.user_id == user_id for p in session.players):
            return session

        # Mid-game join request
        if session.status == "active":
            session.pending_join_requests[user_id] = user_name
            return session

        if session.status not in ("lobby", "countdown"):
            return None

        new_player = self.engine.create_player(user_id, user_name)
        session.players.append(new_player)
        session.last_action_timestamp = time.time()

        # If this is the 2nd player, start countdown
        if len(session.players) == 2 and session.status == "lobby":
            session.status = "countdown"
            session.countdown_seconds = 50

        # Persist to DB
        async with async_session_factory() as db:
            stmt = select(MatchModel).where(MatchModel.match_id == match_id)
            res = await db.execute(stmt)
            db_m = res.scalar_one_or_none()
            if db_m:
                player_ids = [p.user_id for p in session.players]
                db_m.team_b_players = json.dumps(player_ids)
                await db.commit()

        return session

    def extend_countdown(self, match_id: str, seconds: int = 30) -> Optional[int]:
        session = self.get_session(match_id)
        if session and session.status in ("lobby", "countdown"):
            session.countdown_seconds += seconds
            return session.countdown_seconds
        return None

    async def start_match(self, match_id: str) -> Optional[MatchSession]:
        session = self.get_session(match_id)
        if not session or len(session.players) < 2 or len({p.user_id for p in session.players}) < 2:
            return None

        current_task = asyncio.current_task()
        if session.countdown_task and session.countdown_task != current_task:
            session.countdown_task.cancel()
        session.countdown_task = None

        session.status = "active"
        session.current_batter_idx = 0  # Host bats first
        session.current_bowler_idx = 1  # 2nd joined player bowls first
        session.current_turn_ball = 0
        session.players[0].has_batted = True
        session.last_action_timestamp = time.time()

        # Update DB
        async with async_session_factory() as db:
            stmt = select(MatchModel).where(MatchModel.match_id == match_id)
            res = await db.execute(stmt)
            db_m = res.scalar_one_or_none()
            if db_m:
                db_m.status = "active"
                await db.commit()

        return session

    async def accept_midgame_join(self, match_id: str, user_id: int) -> Optional[PlayerSessionStats]:
        session = self.get_session(match_id)
        if not session or session.status != "active":
            return None

        user_name = session.pending_join_requests.pop(user_id, None)
        if not user_name:
            return None

        new_player = self.engine.create_player(user_id, user_name)
        session.players.append(new_player)
        return new_player

    def deny_midgame_join(self, match_id: str, user_id: int) -> Optional[str]:
        session = self.get_session(match_id)
        if session:
            return session.pending_join_requests.pop(user_id, None)
        return None

    async def record_delivery_interaction(
        self,
        match_id: str,
        user_id: int,
        pick: int,
        role: str
    ) -> Dict[str, Any]:
        session = self.get_session(match_id)
        if not session or session.status != "active":
            return {"status": "error", "message": "Match not active"}
        if role not in ("bowler", "batter"):
            return {"status": "error", "message": "Choose a valid batting or bowling action."}
        if not isinstance(pick, int) or not 0 <= pick <= 6:
            return {"status": "error", "message": "Choose a number from 1 to 6."}
        if role == "bowler" and pick == 0:
            return {"status": "error", "message": "A bowler must choose a number from 1 to 6. Please try again."}
        if role == "batter" and pick == 0:
            return {"status": "error", "message": "A Solo batter must choose a number from 1 to 6. Please try again."}

        batter = session.current_batter
        bowler = session.current_bowler
        if not batter or not bowler:
            return {"status": "error", "message": "Invalid match state"}
        if batter.user_id == bowler.user_id:
            return {"status": "error", "message": "The active batter and bowler must be different players."}

        if role == "bowler":
            if user_id != bowler.user_id:
                return {"status": "error", "message": f"Only active bowler ({bowler.name}) can bowl!"}
            session.pending_bowler_pick = pick
            session.last_action_timestamp = time.time()
            return {"status": "bowler_picked", "pick": pick}

        elif role == "batter":
            if user_id != batter.user_id:
                return {"status": "error", "message": f"Only active batter ({batter.name}) can bat!"}
            if session.pending_bowler_pick is None:
                return {"status": "error", "message": "Waiting for bowler to deliver in DM first!"}
            if not 1 <= session.pending_bowler_pick <= 6:
                session.pending_bowler_pick = None
                return {"status": "error", "message": "The bowler must choose a number from 1 to 6. Please ask them to try again."}
            session.pending_batter_pick = pick
            session.last_action_timestamp = time.time()

        # Both picks ready -> resolve ball!
        b_pick = session.pending_bowler_pick
        bat_pick = session.pending_batter_pick
        session.pending_bowler_pick = None
        session.pending_batter_pick = None

        session.current_turn_ball += 1
        b_res = self.engine.process_delivery(
            batter=batter,
            bowler=bowler,
            batter_pick=bat_pick,
            bowler_pick=b_pick,
            turn_ball_num=session.current_turn_ball
        )

        ball_symbol = "W" if b_res.is_wicket else str(b_res.runs)
        session.current_over_balls.append({
            "runs": b_res.runs,
            "is_wicket": b_res.is_wicket,
            "symbol": ball_symbol
        })

        turn_finished = False
        over_completed = False
        over_summary_data = None
        is_match_complete = False
        next_batter = None
        next_bowler = None

        if session.unlimited_overs:
            if b_res.is_wicket:
                # Batter is OUT!
                turn_finished = True
                batter.is_out = True
                is_match_complete = self._advance_to_next_batter(session)
                if not is_match_complete:
                    next_batter = session.current_batter
                    next_bowler = session.current_bowler
            else:
                # Unlimited overs: Batter stays until OUT!
                if session.current_turn_ball >= session.balls_per_turn:
                    over_completed = True
                    over_summary_data = {
                        "bowler_name": bowler.name,
                        "batter_name": batter.name,
                        "runs": sum(b["runs"] for b in session.current_over_balls),
                        "wkts": sum(1 for b in session.current_over_balls if b["is_wicket"]),
                        "balls": [b["symbol"] for b in session.current_over_balls],
                        "batter_runs": batter.runs,
                        "batter_balls": batter.balls,
                        "batter_sr": batter.strike_rate
                    }
                    session.current_over_balls = []
                    session.current_turn_ball = 0
                    num_players = len(session.players)
                    new_bowler_idx = (session.current_bowler_idx + 1) % num_players
                    if new_bowler_idx == session.current_batter_idx and num_players > 1:
                        new_bowler_idx = (new_bowler_idx + 1) % num_players
                    session.current_bowler_idx = new_bowler_idx
                    next_batter = session.current_batter
                    next_bowler = session.current_bowler
        else:
            # Fixed over mode: Wicket OR finished turn balls ends turn
            if b_res.is_wicket or session.current_turn_ball >= session.balls_per_turn:
                turn_finished = True
                batter.is_out = b_res.is_wicket
                if session.current_turn_ball >= session.balls_per_turn:
                    over_completed = True
                    over_summary_data = {
                        "bowler_name": bowler.name,
                        "batter_name": batter.name,
                        "runs": sum(b["runs"] for b in session.current_over_balls),
                        "wkts": sum(1 for b in session.current_over_balls if b["is_wicket"]),
                        "balls": [b["symbol"] for b in session.current_over_balls],
                        "batter_runs": batter.runs,
                        "batter_balls": batter.balls,
                        "batter_sr": batter.strike_rate
                    }
                    session.current_over_balls = []
                is_match_complete = self._advance_to_next_batter(session)
                if not is_match_complete:
                    next_batter = session.current_batter
                    next_bowler = session.current_bowler

        if is_match_complete:
            await self._finalize_match(session)

        return {
            "status": "ball_resolved",
            "ball_result": b_res,
            "batter": batter,
            "bowler": bowler,
            "turn_ball": session.current_turn_ball,
            "turn_finished": turn_finished,
            "over_completed": over_completed,
            "over_summary_data": over_summary_data,
            "next_batter": next_batter,
            "next_bowler": next_bowler,
            "is_match_complete": is_match_complete,
            "winner_name": session.winner_name,
            "session": session
        }

    async def handle_batter_afk_timeout(self, match_id: str) -> Dict[str, Any]:
        """Batter failed to play in time -> Auto OUT or Win by scorecard/forfeit if 2 players."""
        session = self.get_session(match_id)
        if not session or session.status != "active":
            return {"status": "error"}

        batter = session.current_batter
        bowler = session.current_bowler
        if not batter or not bowler:
            return {"status": "error"}

        session.current_turn_ball += 1
        b_res = self.engine.process_batter_afk_timeout(batter, session.current_turn_ball)
        batter.is_out = True

        if len(session.players) == 2:
            session.status = "completed"
            if bowler.has_batted:
                if bowler.runs > batter.runs:
                    winner = bowler
                    reason = f"{bowler.name} won on scorecard ({bowler.runs} vs {batter.runs} runs)"
                elif batter.runs > bowler.runs:
                    winner = batter
                    reason = f"{batter.name} won on scorecard ({batter.runs} vs {bowler.runs} runs)"
                else:
                    winner = bowler
                    reason = f"{bowler.name} won tiebreaker after {batter.name} timed out"
            else:
                winner = bowler
                reason = f"{bowler.name} awarded victory! {batter.name} was AFK and timed out"

            session.winner_id = winner.user_id
            session.winner_name = winner.name
            session.result_summary = reason
            await self._finalize_match_with_winner(session, winner)
            return {
                "status": "match_won_by_afk",
                "winner": winner,
                "afk_player": batter,
                "reason": reason,
                "session": session
            }

        is_match_complete = self._advance_to_next_batter(session)
        if is_match_complete:
            await self._finalize_match(session)

        return {
            "status": "ball_resolved",
            "ball_result": b_res,
            "batter": batter,
            "bowler": bowler,
            "turn_finished": True,
            "over_completed": False,
            "next_batter": session.current_batter,
            "next_bowler": session.current_bowler,
            "is_match_complete": is_match_complete,
            "session": session
        }

    async def handle_bowler_afk_timeout(self, match_id: str) -> Dict[str, Any]:
        """Bowler failed to bowl in time -> Change bowler automatically or award win by scorecard if 2 players."""
        session = self.get_session(match_id)
        if not session or session.status != "active":
            return {"status": "error"}

        old_bowler = session.current_bowler
        batter = session.current_batter

        if len(session.players) == 2:
            session.status = "completed"
            if batter.runs > old_bowler.runs:
                winner = batter
                reason = f"{batter.name} leads on scorecard ({batter.runs} vs {old_bowler.runs} runs)"
            elif batter.runs == old_bowler.runs:
                if batter.strike_rate >= old_bowler.strike_rate:
                    winner = batter
                    reason = f"{batter.name} won on strike rate ({batter.strike_rate:.1f}) after {old_bowler.name} timed out"
                else:
                    winner = batter
                    reason = f"{batter.name} awarded victory after {old_bowler.name} timed out"
            else:
                winner = batter
                reason = f"{batter.name} awarded victory! {old_bowler.name} was AFK and timed out"

            session.winner_id = winner.user_id
            session.winner_name = winner.name
            session.result_summary = f"{winner.name} won! {reason}"
            await self._finalize_match_with_winner(session, winner)
            return {
                "status": "match_won_by_afk",
                "winner": winner,
                "afk_player": old_bowler,
                "reason": reason,
                "session": session
            }

        # Rotate bowler to next player who is NOT the batter
        num_players = len(session.players)
        new_bowler_idx = (session.current_bowler_idx + 1) % num_players
        if new_bowler_idx == session.current_batter_idx and num_players > 1:
            new_bowler_idx = (new_bowler_idx + 1) % num_players

        session.current_bowler_idx = new_bowler_idx
        new_bowler = session.current_bowler
        session.pending_bowler_pick = None

        return {
            "status": "bowler_changed",
            "old_bowler": old_bowler,
            "new_bowler": new_bowler,
            "session": session
        }

    def _advance_to_next_batter(self, session: MatchSession) -> bool:
        """Finds next player who hasn't batted. Returns True if all have batted."""
        session.current_turn_ball = 0
        session.current_over_balls = []
        session.pending_bowler_pick = None
        session.pending_batter_pick = None

        # Look for next unbatted player
        unbatted_idx = None
        for i, p in enumerate(session.players):
            if not p.has_batted and not p.is_out:
                unbatted_idx = i
                break

        if unbatted_idx is None:
            # All players completed their turn!
            return True

        session.current_batter_idx = unbatted_idx
        session.players[unbatted_idx].has_batted = True

        # Rotate bowler to next player who is NOT the batter
        num_players = len(session.players)
        new_bowler_idx = (unbatted_idx + 1) % num_players
        if new_bowler_idx == unbatted_idx and num_players > 1:
            new_bowler_idx = (new_bowler_idx + 1) % num_players
        session.current_bowler_idx = new_bowler_idx
        return False

    async def _finalize_match(self, session: MatchSession):
        session.status = "completed"
        # Find player with highest runs (and most wickets tiebreaker)
        sorted_players = sorted(
            session.players,
            key=lambda p: (p.runs, p.wickets, -p.runs_conceded),
            reverse=True
        )
        winner = sorted_players[0]
        session.winner_id = winner.user_id
        session.winner_name = winner.name
        session.result_summary = f"{winner.name} won with {winner.runs} runs!"

        # Credit rewards & update DB career stats safely
        try:
            for p in session.players:
                is_winner = (p.user_id == winner.user_id)
                c_rew = 500 if is_winner else 100
                r_rew = 5 if is_winner else 1
                await economy_service.add_rewards(
                    user_id=p.user_id,
                    coins=c_rew,
                    rubies=r_rew,
                    idempotency_key=f"solo_match_{session.match_id}_{p.user_id}"
                )
                await economy_service.record_match_stats(
                    user_id=p.user_id,
                    is_win=is_winner,
                    runs=p.runs,
                    wickets=p.wickets,
                    fours=p.fours,
                    sixes=p.sixes,
                    match_id=f"solo:{session.match_id}",
                    mode="solo",
                    display_name=p.name,
                    ducks=int(p.is_out and p.runs == 0),
                    balls_faced=p.balls,
                    balls_bowled=p.balls_bowled,
                    runs_conceded=p.runs_conceded,
                )

            # Update Match in DB
            async with async_session_factory() as db:
                stmt = select(MatchModel).where(MatchModel.match_id == session.match_id)
                res = await db.execute(stmt)
                db_m = res.scalar_one_or_none()
                if db_m:
                    db_m.status = "completed"
                    db_m.winner_user_id = winner.user_id
                    await db.commit()
        except Exception:
            pass

    async def _finalize_match_with_winner(self, session: MatchSession, winner: PlayerSessionStats):
        session.status = "completed"
        session.winner_id = winner.user_id
        session.winner_name = winner.name

        # Credit rewards & update DB career stats safely
        try:
            for p in session.players:
                is_winner = (p.user_id == winner.user_id)
                c_rew = 500 if is_winner else 100
                r_rew = 5 if is_winner else 1
                await economy_service.add_rewards(
                    user_id=p.user_id,
                    coins=c_rew,
                    rubies=r_rew,
                    idempotency_key=f"solo_match_{session.match_id}_{p.user_id}"
                )
                await economy_service.record_match_stats(
                    user_id=p.user_id,
                    is_win=is_winner,
                    runs=p.runs,
                    wickets=p.wickets,
                    fours=p.fours,
                    sixes=p.sixes,
                    match_id=f"solo:{session.match_id}",
                    mode="solo",
                    display_name=p.name,
                    ducks=int(p.is_out and p.runs == 0),
                    balls_faced=p.balls,
                    balls_bowled=p.balls_bowled,
                    runs_conceded=p.runs_conceded,
                )

            # Update Match in DB
            async with async_session_factory() as db:
                stmt = select(MatchModel).where(MatchModel.match_id == session.match_id)
                res = await db.execute(stmt)
                db_m = res.scalar_one_or_none()
                if db_m:
                    db_m.status = "completed"
                    db_m.winner_user_id = winner.user_id
                    await db.commit()
        except Exception:
            pass

match_service = MatchService()
