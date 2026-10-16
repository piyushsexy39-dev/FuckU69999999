from typing import Dict, Any, Optional

class BettingService:
    def __init__(self):
        self.bets: Dict[str, Dict[str, Any]] = {}

    def place_bet(self, bet_id: str, match_id: str, user_id: int, amount: int, pick: str) -> Dict[str, Any]:
        bet = {
            "id": bet_id,
            "match_id": match_id,
            "user_id": user_id,
            "amount": amount,
            "pick": pick,
            "status": "pending",
            "payout": 0
        }
        self.bets[bet_id] = bet
        return bet

    def resolve_bet(self, bet_id: str, winning_pick: str, multiplier: float = 1.85) -> Optional[Dict[str, Any]]:
        bet = self.bets.get(bet_id)
        if not bet or bet["status"] != "pending":
            return None
        if bet["pick"] == winning_pick:
            bet["status"] = "won"
            bet["payout"] = int(bet["amount"] * multiplier)
        else:
            bet["status"] = "lost"
            bet["payout"] = 0
        return bet

betting_service = BettingService()
