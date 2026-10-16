from typing import List, Dict

COMMAND_MANIFEST: List[Dict[str, str]] = [
    {"command": "start", "description": "Start Ultimate Cricket and view game menu"},
    {"command": "help", "description": "View game rules, commands and guide"},
    {"command": "solo", "description": "Create a Solo Hand Cricket match lobby"},
    {"command": "add_solo", "description": "Alias: Create a Solo match lobby"},
    {"command": "addsolo", "description": "Alias: Create a Solo match lobby"},
    {"command": "joingame", "description": "Join active game lobby"},
    {"command": "joinsolo", "description": "Join active solo lobby"},
    {"command": "leavesolo", "description": "Leave current solo lobby"},
    {"command": "removesolo", "description": "Cancel current solo lobby"},
    {"command": "forcestart", "description": "Host/Admin: Force start the match"},
    {"command": "cancel", "description": "Cancel active match or lobby"},
    {"command": "team", "description": "Team match lobby and squad menu"},
    {"command": "stats", "description": "View your career stats & win rate"},
    {"command": "profile", "description": "View player card and rank"},
    {"command": "balance", "description": "Check your Coins and Rubies balance"},
    {"command": "shop", "description": "Browse the Gully Bazaar shop"},
    {"command": "inventory", "description": "View your equipped bats and perks"},
    {"command": "tournament", "description": "Gully Premier League (GPL) overview"},
    {"command": "auction", "description": "Player Auction room"},
    {"command": "leaderboard", "description": "Top mohalla batsmen and bowlers"}
]
