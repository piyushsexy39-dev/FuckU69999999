from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class TeamLineup:
    team_name: str
    player_ids: List[int]
    captain_id: int
    current_batter_idx: int = 0
    non_striker_idx: int = 1
    current_bowler_id: Optional[int] = None
    bowler_overs: Dict[int, int] = field(default_factory=dict)

class TeamMatchEngine:
    def __init__(self, balls_per_over: int = 6, max_overs: int = 2):
        self.balls_per_over = balls_per_over
        self.max_overs = max_overs

    def rotate_strike(self, lineup: TeamLineup):
        """Swaps striker and non-striker."""
        temp = lineup.current_batter_idx
        lineup.current_batter_idx = lineup.non_striker_idx
        lineup.non_striker_idx = temp

    def get_next_batter(self, lineup: TeamLineup) -> Optional[int]:
        """Returns the next batter ID in the order."""
        next_idx = max(lineup.current_batter_idx, lineup.non_striker_idx) + 1
        if next_idx < len(lineup.player_ids):
            lineup.current_batter_idx = next_idx
            return lineup.player_ids[next_idx]
        return None

    def validate_bowler(self, lineup: TeamLineup, bowler_id: int, max_overs_per_bowler: int = 1) -> bool:
        """Validates if bowler has overs remaining."""
        bowled = lineup.bowler_overs.get(bowler_id, 0)
        return bowled < max_overs_per_bowler
