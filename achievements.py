from typing import List, Dict, Any

ACHIEVEMENTS = [
    {"id": "don", "title": "Gully Ka Don", "desc": "Win 5 Gully Cricket matches", "req_wins": 5, "reward_coins": 2000},
    {"id": "six_machine", "title": "Khidki Specialist", "desc": "Hit 10 sixes", "req_sixes": 10, "reward_coins": 1500},
    {"id": "yorker_king", "title": "Toe Crusher", "desc": "Take 5 wickets", "req_wickets": 5, "reward_coins": 2000},
    {"id": "century", "title": "Century in Mohalla", "desc": "Score 50+ runs", "req_runs": 50, "reward_coins": 5000},
]

class AchievementService:
    @staticmethod
    def get_user_achievements(matches_won: int, total_sixes: int, total_wickets: int, highest_score: int) -> List[Dict[str, Any]]:
        unlocked = []
        for a in ACHIEVEMENTS:
            is_done = False
            if "req_wins" in a and matches_won >= a["req_wins"]:
                is_done = True
            elif "req_sixes" in a and total_sixes >= a["req_sixes"]:
                is_done = True
            elif "req_wickets" in a and total_wickets >= a["req_wickets"]:
                is_done = True
            elif "req_runs" in a and highest_score >= a["req_runs"]:
                is_done = True

            unlocked.append({
                **a,
                "unlocked": is_done
            })
        return unlocked

achievement_service = AchievementService()
