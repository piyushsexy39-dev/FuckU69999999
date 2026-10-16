from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

@dataclass
class BallResult:
    ball_number: int       # 1 to balls_per_turn
    batter_pick: int
    bowler_pick: int
    runs: int = 0
    is_wicket: bool = False
    is_afk: bool = False
    commentary_hi: str = ""
    commentary_en: str = ""

@dataclass
class PlayerSessionStats:
    user_id: int
    name: str
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    is_out: bool = False
    wickets: int = 0
    runs_conceded: int = 0
    balls_bowled: int = 0
    has_batted: bool = False
    is_afk: bool = False

    @property
    def strike_rate(self) -> float:
        return (self.runs / max(1, self.balls)) * 100.0 if self.balls > 0 else 0.0

    @property
    def overs_bowled(self) -> str:
        completed = self.balls_bowled // 6
        rem = self.balls_bowled % 6
        return f"{completed}.{rem}"

    @property
    def economy(self) -> float:
        return (self.runs_conceded / max(1, self.balls_bowled)) * 6.0 if self.balls_bowled > 0 else 0.0

@dataclass
class MultiplayerSoloEngine:
    balls_per_turn: int = 6       # 3 or 6 balls per turn
    max_overs_per_turn: int = 1

    def create_player(self, user_id: int, name: str) -> PlayerSessionStats:
        return PlayerSessionStats(user_id=user_id, name=name)

    def process_delivery(
        self,
        batter: PlayerSessionStats,
        bowler: PlayerSessionStats,
        batter_pick: int,
        bowler_pick: int,
        turn_ball_num: int
    ) -> BallResult:
        """
        Hand cricket delivery resolution:
        If batter_pick == bowler_pick -> OUT!
        Else -> runs = batter_pick
        """
        if not 1 <= batter_pick <= 6:
            raise ValueError("A Solo batter must choose a number from 1 to 6.")
        if not 1 <= bowler_pick <= 6:
            raise ValueError("A bowler must choose a number from 1 to 6.")

        batter.balls += 1
        bowler.balls_bowled += 1

        if batter_pick == bowler_pick:
            # Wicket!
            batter.is_out = True
            bowler.wickets += 1
            return BallResult(
                ball_number=turn_ball_num,
                batter_pick=batter_pick,
                bowler_pick=bowler_pick,
                runs=0,
                is_wicket=True,
                commentary_en="The stadium erupts! The batter is stunned!"
            )
        else:
            # Runs scored
            runs = batter_pick
            batter.runs += runs
            bowler.runs_conceded += runs
            if runs == 4:
                batter.fours += 1
            elif runs == 6:
                batter.sixes += 1

            return BallResult(
                ball_number=turn_ball_num,
                batter_pick=batter_pick,
                bowler_pick=bowler_pick,
                runs=runs,
                is_wicket=False,
                commentary_en=f"{runs} run(s) scored."
            )

    def process_batter_afk_timeout(self, batter: PlayerSessionStats, turn_ball_num: int) -> BallResult:
        """Batter timed out (AFK) -> Auto OUT!"""
        batter.is_out = True
        batter.is_afk = True
        return BallResult(
            ball_number=turn_ball_num,
            batter_pick=0,
            bowler_pick=0,
            runs=0,
            is_wicket=True,
            is_afk=True,
            commentary_en="Batter timed out! Auto OUT by the umpire!"
        )
