from typing import List, Dict, Any, Optional

class AuctionService:
    def __init__(self):
        self.active_auctions: Dict[str, Dict[str, Any]] = {}

    def start_auction(self, auction_id: str, player_name: str, base_price: int = 25000) -> Dict[str, Any]:
        auction = {
            "id": auction_id,
            "player_name": player_name,
            "base_price": base_price,
            "current_bid": base_price,
            "leading_user_id": None,
            "is_closed": False,
        }
        self.active_auctions[auction_id] = auction
        return auction

    def place_bid(self, auction_id: str, user_id: int, bid_amount: int) -> Optional[Dict[str, Any]]:
        auction = self.active_auctions.get(auction_id)
        if not auction or auction["is_closed"]:
            return None
        if bid_amount <= auction["current_bid"]:
            return None

        auction["current_bid"] = bid_amount
        auction["leading_user_id"] = user_id
        return auction

    def close_auction(self, auction_id: str) -> Optional[Dict[str, Any]]:
        auction = self.active_auctions.get(auction_id)
        if auction:
            auction["is_closed"] = True
        return auction

auction_service = AuctionService()
