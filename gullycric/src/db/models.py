import datetime
import json
from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime, Float, Text
from .base import Base

class User(Base):
    __tablename__ = "users"

    user_id = Column(BigInteger, primary_key=True)
    username = Column(String(64), nullable=True)
    first_name = Column(String(128), default="Player")
    diamonds = Column(Integer, default=100)
    matches_played = Column(Integer, default=0)
    matches_won = Column(Integer, default=0)
    total_runs = Column(Integer, default=0)
    total_wickets = Column(Integer, default=0)
    highest_score = Column(Integer, default=0)
    total_sixes = Column(Integer, default=0)
    total_fours = Column(Integer, default=0)
    total_ducks = Column(Integer, default=0)
    balls_faced = Column(Integer, default=0)
    balls_bowled = Column(Integer, default=0)
    runs_conceded = Column(Integer, default=0)
    solo_matches = Column(Integer, default=0)
    team_matches = Column(Integer, default=0)
    started_bot = Column(Boolean, default=False, nullable=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class GroupChat(Base):
    __tablename__ = "group_chats"

    chat_id = Column(BigInteger, primary_key=True)
    title = Column(String(256), nullable=False, default="Group")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )


class Match(Base):
    __tablename__ = "matches"

    match_id = Column(String(64), primary_key=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    mode = Column(String(32), default="solo")  # solo, team
    status = Column(String(32), default="lobby")  # lobby, active, completed, cancelled
    host_id = Column(BigInteger, nullable=False)
    balls_per_over = Column(Integer, default=3)
    max_overs = Column(Integer, default=1)
    
    # JSON serialized structures
    team_a_players = Column(Text, default="[]")  # List of user IDs
    team_b_players = Column(Text, default="[]")  # List of user IDs
    match_state = Column(Text, default="{}")     # Active game engine state
    
    current_innings = Column(Integer, default=1)
    target = Column(Integer, nullable=True)
    winner_id = Column(BigInteger, nullable=True)
    version = Column(Integer, default=1)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class MatchPlayerRecord(Base):
    __tablename__ = "match_player_records"

    match_id = Column(String(64), primary_key=True)
    user_id = Column(BigInteger, primary_key=True)
    display_name = Column(String(128), nullable=False, default="Player")
    mode = Column(String(32), nullable=False)
    runs = Column(Integer, nullable=False, default=0)
    wickets = Column(Integer, nullable=False, default=0)
    fours = Column(Integer, nullable=False, default=0)
    sixes = Column(Integer, nullable=False, default=0)
    ducks = Column(Integer, nullable=False, default=0)
    balls_faced = Column(Integer, nullable=False, default=0)
    balls_bowled = Column(Integer, nullable=False, default=0)
    runs_conceded = Column(Integer, nullable=False, default=0)
    is_win = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class RewardLedger(Base):
    __tablename__ = "reward_ledger"

    idempotency_key = Column(String(160), primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    diamonds = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True, nullable=False)
    item_id = Column(String(64), nullable=False)
    item_type = Column(String(32), nullable=False)  # bat, ball, perk
    equipped = Column(Boolean, default=False)
    acquired_at = Column(DateTime, default=datetime.datetime.utcnow)

class Tournament(Base):
    __tablename__ = "tournaments"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    status = Column(String(32), default="registration")  # registration, in_progress, completed
    max_teams = Column(Integer, default=4)
    fixtures_data = Column(Text, default="[]")
    standings_data = Column(Text, default="{}")
    champion_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Bet(Base):
    __tablename__ = "bets"

    id = Column(String(64), primary_key=True)
    match_id = Column(String(64), index=True, nullable=False)
    user_id = Column(BigInteger, index=True, nullable=False)
    amount = Column(Integer, nullable=False)
    market = Column(String(64), nullable=False)
    choice = Column(String(64), nullable=False)
    status = Column(String(32), default="pending")  # pending, won, lost
    payout = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Moderator(Base):
    __tablename__ = "moderators"

    user_id = Column(BigInteger, primary_key=True)
    username = Column(String(64), nullable=True)
    first_name = Column(String(128), default="Moderator")
    added_by = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class BannedUser(Base):
    __tablename__ = "banned_users"

    user_id = Column(BigInteger, primary_key=True)
    username = Column(String(64), nullable=True)
    first_name = Column(String(128), default="Player")
    banned_by = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
