import logging
import random
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("gullycric.team")

@dataclass
class TeamPlayer:
    user_id: int
    name: str
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    wickets: int = 0
    runs_conceded: int = 0
    balls_bowled: int = 0
    is_out: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "name": self.name,
            "runs": self.runs,
            "balls": self.balls,
            "fours": self.fours,
            "sixes": self.sixes,
            "wickets": self.wickets,
            "runs_conceded": self.runs_conceded,
            "balls_bowled": self.balls_bowled,
            "is_out": self.is_out,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TeamPlayer":
        return cls(
            user_id=d["user_id"],
            name=d.get("name", "Player"),
            runs=d.get("runs", 0),
            balls=d.get("balls", 0),
            fours=d.get("fours", 0),
            sixes=d.get("sixes", 0),
            wickets=d.get("wickets", 0),
            runs_conceded=d.get("runs_conceded", 0),
            balls_bowled=d.get("balls_bowled", 0),
            is_out=d.get("is_out", False),
        )

@dataclass
class TeamMatchSession:
    match_id: str
    chat_id: int
    host_id: Optional[int] = None
    host_name: str = ""
    host_claimed: bool = False
    
    # States: lobby, awaiting_overs, choosing_captains, awaiting_toss, toss_decision, active, completed, cancelled
    status: str = "lobby"
    max_overs: int = 2
    balls_per_over: int = 6
    spamfree: bool = False
    spamfree_last_bowler_id: Optional[int] = None
    spamfree_last_bowler_pick: Optional[int] = None
    spamfree_consecutive_count: int = 0
    
    team_a_name: str = "Team A 🔵"
    team_b_name: str = "Team B 🔴"
    team_a_players: List[TeamPlayer] = field(default_factory=list)
    team_b_players: List[TeamPlayer] = field(default_factory=list)
    captain_a_id: Optional[int] = None
    captain_b_id: Optional[int] = None
    
    toss_winner: Optional[str] = None  # "team_a" or "team_b"
    toss_winner_captain_id: Optional[int] = None
    toss_decision: Optional[str] = None  # "Batting" or "Bowling"
    toss_caller_id: Optional[int] = None
    toss_call: Optional[str] = None
    toss_result: Optional[str] = None
    
    batting_team: str = "team_a"  # "team_a" or "team_b"
    bowling_team: str = "team_b"
    
    target: int = 0
    current_innings: int = 1
    total_runs_a: int = 0
    wickets_a: int = 0
    balls_bowled_a: int = 0
    total_runs_b: int = 0
    wickets_b: int = 0
    balls_bowled_b: int = 0
    
    striker_idx: Optional[int] = None
    non_striker_idx: Optional[int] = None
    current_bowler_id: Optional[int] = None
    previous_over_bowler_id: Optional[int] = None
    striker_faced_ball: bool = False
    turn_timer_task: Optional[Any] = None
    
    pending_bowler_pick: Optional[int] = None
    ball_in_over: int = 0
    current_over_deliveries: List[Dict[str, Any]] = field(default_factory=list)
    banned_user_ids: List[int] = field(default_factory=list)
    live_score_message_id: Optional[int] = None
    live_score_text: Optional[str] = None
    final_result: Optional[str] = None
    celebrated_milestones: set[tuple[int, int, int]] = field(default_factory=set)
    sent_media_events: set[str] = field(default_factory=set)

    def get_batting_roster(self) -> List[TeamPlayer]:
        return self.team_a_players if self.batting_team == "team_a" else self.team_b_players

    def get_bowling_roster(self) -> List[TeamPlayer]:
        return self.team_b_players if self.batting_team == "team_a" else self.team_a_players

    def get_current_striker(self) -> Optional[TeamPlayer]:
        roster = self.get_batting_roster()
        if self.striker_idx is not None and 0 <= self.striker_idx < len(roster):
            p = roster[self.striker_idx]
            if not p.is_out:
                return p
        return None

    def get_current_non_striker(self) -> Optional[TeamPlayer]:
        roster = self.get_batting_roster()
        if self.non_striker_idx is not None and 0 <= self.non_striker_idx < len(roster):
            p = roster[self.non_striker_idx]
            if not p.is_out:
                return p
        return None

    def rotate_strike(self):
        """Swaps striker and non-striker at crease only if both are present."""
        if self.striker_idx is not None and self.non_striker_idx is not None:
            self.striker_idx, self.non_striker_idx = self.non_striker_idx, self.striker_idx

    def needs_non_striker(self) -> bool:
        """Non-striker is only needed if team has more than 1 player."""
        roster = self.get_batting_roster()
        return len(roster) > 1

    def is_all_out(self) -> bool:
        """Determines if the batting team is all out. No last man standing rule: at least 2 active players needed if roster has >= 2."""
        roster = self.get_batting_roster()
        if len(roster) <= 1:
            curr_w = self.wickets_a if self.batting_team == "team_a" else self.wickets_b
            return curr_w >= len(roster)
        remaining = [p for p in roster if not p.is_out]
        return len(remaining) < 2

    def get_current_bowler(self) -> Optional[TeamPlayer]:
        if self.current_bowler_id is None:
            return None
        roster = self.get_bowling_roster()
        for p in roster:
            if p.user_id == self.current_bowler_id:
                return p
        return None

    def get_batting_captain_id(self) -> Optional[int]:
        return self.captain_a_id if self.batting_team == "team_a" else self.captain_b_id

    def get_bowling_captain_id(self) -> Optional[int]:
        return self.captain_b_id if self.batting_team == "team_a" else self.captain_a_id

    def get_batting_captain_name(self) -> str:
        cap_id = self.get_batting_captain_id()
        roster = self.get_batting_roster()
        for p in roster:
            if p.user_id == cap_id:
                return p.name
        return "Captain"

    def get_bowling_captain_name(self) -> str:
        cap_id = self.get_bowling_captain_id()
        roster = self.get_bowling_roster()
        for p in roster:
            if p.user_id == cap_id:
                return p.name
        return "Captain"

    def add_player(self, team: str, user_id: int, name: str):
        if team.lower() in ("a", "team_a"):
            self.team_b_players = [p for p in self.team_b_players if p.user_id != user_id]
            if not any(p.user_id == user_id for p in self.team_a_players):
                self.team_a_players.append(TeamPlayer(user_id=user_id, name=name))
        else:
            self.team_a_players = [p for p in self.team_a_players if p.user_id != user_id]
            if not any(p.user_id == user_id for p in self.team_b_players):
                self.team_b_players.append(TeamPlayer(user_id=user_id, name=name))

    def calculate_potm(self) -> Optional[TeamPlayer]:
        all_players = self.team_a_players + self.team_b_players
        if not all_players:
            return None
        return max(all_players, key=lambda p: (p.runs * 1.5 + p.wickets * 25 + p.fours * 2 + p.sixes * 4))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": "team",
            "match_id": self.match_id,
            "chat_id": self.chat_id,
            "host_id": self.host_id,
            "host_name": self.host_name,
            "host_claimed": self.host_claimed,
            "status": self.status,
            "max_overs": self.max_overs,
            "balls_per_over": self.balls_per_over,
            "spamfree": self.spamfree,
            "spamfree_last_bowler_id": self.spamfree_last_bowler_id,
            "spamfree_last_bowler_pick": self.spamfree_last_bowler_pick,
            "spamfree_consecutive_count": self.spamfree_consecutive_count,
            "team_a_name": self.team_a_name,
            "team_b_name": self.team_b_name,
            "team_a_players": [p.to_dict() for p in self.team_a_players],
            "team_b_players": [p.to_dict() for p in self.team_b_players],
            "captain_a_id": self.captain_a_id,
            "captain_b_id": self.captain_b_id,
            "toss_winner": self.toss_winner,
            "toss_winner_captain_id": self.toss_winner_captain_id,
            "toss_decision": self.toss_decision,
            "toss_caller_id": self.toss_caller_id,
            "toss_call": self.toss_call,
            "toss_result": self.toss_result,
            "batting_team": self.batting_team,
            "bowling_team": self.bowling_team,
            "target": self.target,
            "current_innings": self.current_innings,
            "total_runs_a": self.total_runs_a,
            "wickets_a": self.wickets_a,
            "balls_bowled_a": self.balls_bowled_a,
            "total_runs_b": self.total_runs_b,
            "wickets_b": self.wickets_b,
            "balls_bowled_b": self.balls_bowled_b,
            "striker_idx": self.striker_idx,
            "non_striker_idx": self.non_striker_idx,
            "current_bowler_id": self.current_bowler_id,
            "previous_over_bowler_id": self.previous_over_bowler_id,
            "striker_faced_ball": self.striker_faced_ball,
            "ball_in_over": self.ball_in_over,
            "current_over_deliveries": self.current_over_deliveries,
            "live_score_message_id": self.live_score_message_id,
            "live_score_text": self.live_score_text,
            "final_result": self.final_result,
            "celebrated_milestones": [
                list(milestone) for milestone in self.celebrated_milestones
            ],
            "sent_media_events": sorted(self.sent_media_events),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TeamMatchSession":
        session = cls(
            match_id=d.get("match_id", f"tm_{random.randint(10000, 99999)}"),
            chat_id=d.get("chat_id", 0),
            host_id=d.get("host_id"),
            host_name=d.get("host_name", "Host"),
            host_claimed=d.get("host_claimed", True),
            status=d.get("status", "active"),
            max_overs=d.get("max_overs", 2),
            balls_per_over=d.get("balls_per_over", 6),
            spamfree=d.get("spamfree", False),
            spamfree_last_bowler_id=d.get("spamfree_last_bowler_id"),
            spamfree_last_bowler_pick=d.get("spamfree_last_bowler_pick"),
            spamfree_consecutive_count=d.get("spamfree_consecutive_count", 0),
            team_a_name=d.get("team_a_name", "Team A 🔵"),
            team_b_name=d.get("team_b_name", "Team B 🔴"),
            team_a_players=[TeamPlayer.from_dict(p) for p in d.get("team_a_players", [])],
            team_b_players=[TeamPlayer.from_dict(p) for p in d.get("team_b_players", [])],
            captain_a_id=d.get("captain_a_id"),
            captain_b_id=d.get("captain_b_id"),
            toss_winner=d.get("toss_winner"),
            toss_winner_captain_id=d.get("toss_winner_captain_id"),
            toss_decision=d.get("toss_decision"),
            toss_caller_id=d.get("toss_caller_id"),
            toss_call=d.get("toss_call"),
            toss_result=d.get("toss_result"),
            batting_team=d.get("batting_team", "team_a"),
            bowling_team=d.get("bowling_team", "team_b"),
            target=d.get("target", 0),
            current_innings=d.get("current_innings", 1),
            total_runs_a=d.get("total_runs_a", 0),
            wickets_a=d.get("wickets_a", 0),
            balls_bowled_a=d.get("balls_bowled_a", 0),
            total_runs_b=d.get("total_runs_b", 0),
            wickets_b=d.get("wickets_b", 0),
            balls_bowled_b=d.get("balls_bowled_b", 0),
            striker_idx=d.get("striker_idx"),
            non_striker_idx=d.get("non_striker_idx"),
            current_bowler_id=d.get("current_bowler_id"),
            previous_over_bowler_id=d.get("previous_over_bowler_id"),
            striker_faced_ball=d.get(
                "striker_faced_ball",
                bool(d.get("ball_in_over", 0) and d.get("striker_idx") is not None),
            ),
            ball_in_over=d.get("ball_in_over", 0),
            current_over_deliveries=d.get("current_over_deliveries", []),
            live_score_message_id=d.get("live_score_message_id"),
            live_score_text=d.get("live_score_text"),
            final_result=d.get("final_result"),
            celebrated_milestones={
                tuple(milestone)
                for milestone in d.get("celebrated_milestones", [])
            },
            sent_media_events=set(d.get("sent_media_events", [])),
        )
        return session

class TeamMatchService:
    def __init__(self):
        self.active_sessions: Dict[int, TeamMatchSession] = {}

    def get_session(self, chat_id: int) -> Optional[TeamMatchSession]:
        return self.active_sessions.get(chat_id)

    def create_session(self, chat_id: int, initiator_id: Optional[int] = None, initiator_name: str = "") -> Optional[TeamMatchSession]:
        from .match_service import match_service

        if match_service.get_active_session_for_chat(chat_id):
            return None
        existing = self.get_session(chat_id)
        if existing and existing.status in (
            "lobby", "awaiting_overs", "choosing_captains", "awaiting_toss",
            "toss_call", "toss_decision", "active",
        ):
            return None
        match_id = f"tm_{int(random.randint(10000, 99999))}"
        session = TeamMatchSession(
            match_id=match_id,
            chat_id=chat_id,
            host_id=initiator_id,
            host_name=initiator_name,
            host_claimed=bool(initiator_id),
            status="lobby"
        )
        self.active_sessions[chat_id] = session
        return session

    def remove_session(self, chat_id: int):
        if chat_id in self.active_sessions:
            del self.active_sessions[chat_id]

team_match_service = TeamMatchService()
