from typing import List, Dict, Any, Optional

class SquadService:
    def __init__(self):
        # Dynamically tracks team rosters created by real players in groups
        self.teams: Dict[str, Dict[str, Any]] = {}

    def get_all_teams(self) -> List[Dict[str, Any]]:
        return list(self.teams.values())

    def get_or_create_team(self, team_id: str, team_name: str, captain_id: int, captain_name: str) -> Dict[str, Any]:
        if team_id not in self.teams:
            self.teams[team_id] = {
                "id": team_id,
                "name": team_name,
                "badge": "⚡",
                "captain_id": captain_id,
                "captain_name": captain_name,
                "players": [captain_name]
            }
        return self.teams[team_id]

    def add_player(self, team_id: str, player_name: str):
        if team_id in self.teams:
            if player_name not in self.teams[team_id]["players"]:
                self.teams[team_id]["players"].append(player_name)

squad_service = SquadService()
