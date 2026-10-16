from typing import List, Dict, Any, Optional

class TournamentWizard:
    @staticmethod
    def generate_schedule(team_ids: List[str]) -> List[Dict[str, Any]]:
        fixtures = []
        match_no = 1
        for i in range(len(team_ids)):
            for j in range(i + 1, len(team_ids)):
                fixtures.append({
                    "match_id": f"fix_{match_no}",
                    "match_no": match_no,
                    "team_a": team_ids[i],
                    "team_b": team_ids[j],
                    "status": "scheduled",
                    "winner": None
                })
                match_no += 1
        return fixtures

class TournamentService:
    def __init__(self):
        self.tournaments: Dict[str, Dict[str, Any]] = {}

    def create_tournament(self, tournament_id: str, name: str, team_ids: List[str]) -> Dict[str, Any]:
        fixtures = TournamentWizard.generate_schedule(team_ids)
        standings = {
            t_id: {"played": 0, "won": 0, "lost": 0, "points": 0, "nrr": 0.0}
            for t_id in team_ids
        }
        tourney = {
            "id": tournament_id,
            "name": name,
            "fixtures": fixtures,
            "standings": standings,
            "status": "active"
        }
        self.tournaments[tournament_id] = tourney
        return tourney

    def update_match_result(self, tournament_id: str, match_no: int, winner_id: str, loser_id: str):
        t = self.tournaments.get(tournament_id)
        if not t:
            return
        for f in t["fixtures"]:
            if f["match_no"] == match_no:
                f["status"] = "completed"
                f["winner"] = winner_id

        t["standings"][winner_id]["played"] += 1
        t["standings"][winner_id]["won"] += 1
        t["standings"][winner_id]["points"] += 2

        t["standings"][loser_id]["played"] += 1
        t["standings"][loser_id]["lost"] += 1

tournament_service = TournamentService()
