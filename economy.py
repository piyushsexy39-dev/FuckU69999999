from sqlalchemy import select
from ..db.session import async_session_factory
from ..db.models import User, MatchPlayerRecord, RewardLedger
from ..utils.idempotency import idempotency
import logging

logger = logging.getLogger("gullycric.economy")

class EconomyService:
    @staticmethod
    async def get_or_create_user(
        user_id: int,
        username: str = None,
        first_name: str = "Player",
        started_bot: bool = False,
    ) -> User:
        cleaned_username = username.strip() if isinstance(username, str) and username.strip() else None
        cleaned_first_name = first_name.strip() if isinstance(first_name, str) and first_name.strip() else "Player"

        async with async_session_factory() as session:
            stmt = select(User).where(User.user_id == user_id)
            res = await session.execute(stmt)
            user = res.scalar_one_or_none()
            if not user:
                user = User(
                    user_id=user_id,
                    username=cleaned_username,
                    first_name=cleaned_first_name,
                    diamonds=100,
                    started_bot=started_bot,
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
            else:
                if cleaned_username and user.username != cleaned_username:
                    user.username = cleaned_username
                if cleaned_first_name and (not user.first_name or user.first_name == "Player" or user.first_name.strip() == "Player"):
                    user.first_name = cleaned_first_name
                if user.username is None and cleaned_username:
                    user.username = cleaned_username
                if started_bot:
                    user.started_bot = True
                await session.commit()
            return user

    @staticmethod
    async def add_rewards(user_id: int, diamonds: int = 0, coins: int = 0, rubies: int = 0, idempotency_key: str = None, **kwargs) -> bool:
        amt = diamonds or coins or rubies or 0
        try:
            async with async_session_factory() as session:
                if idempotency_key:
                    ledger_entry = await session.get(RewardLedger, idempotency_key)
                    if ledger_entry:
                        return False
                stmt = select(User).where(User.user_id == user_id)
                res = await session.execute(stmt)
                user = res.scalar_one_or_none()
                if not user:
                    user = User(
                        user_id=user_id,
                        first_name="Player",
                        diamonds=100 + amt,
                    )
                    session.add(user)
                else:
                    user.diamonds = (user.diamonds or 0) + amt
                if user and user.first_name == "Player" and kwargs.get("first_name"):
                    user.first_name = kwargs["first_name"]
                if idempotency_key:
                    session.add(RewardLedger(
                        idempotency_key=idempotency_key,
                        user_id=user_id,
                        diamonds=amt,
                    ))
                await session.commit()
                return True
        except Exception:
            logger.exception("Could not add reward for user %s", user_id)
            return False

    @staticmethod
    async def record_match_stats(
        user_id: int,
        is_win: bool,
        runs: int = 0,
        wickets: int = 0,
        sixes: int = 0,
        fours: int = 0,
        idempotency_key: str = None,
        match_id: str = None,
        mode: str = "solo",
        display_name: str = "Player",
        ducks: int = 0,
        balls_faced: int = 0,
        balls_bowled: int = 0,
        runs_conceded: int = 0,
        award_diamonds: bool = True,
        **kwargs
    ) -> bool:
        if not match_id and idempotency_key and not idempotency.mark_processed(idempotency_key):
            return False

        try:
            async with async_session_factory() as session:
                if match_id:
                    record = await session.get(MatchPlayerRecord, (match_id, user_id))
                    if record:
                        return False
                stmt = select(User).where(User.user_id == user_id)
                res = await session.execute(stmt)
                user = res.scalar_one_or_none()
                if not user:
                    user = User(
                        user_id=user_id,
                        first_name=display_name or "Player",
                        diamonds=100,
                    )
                    session.add(user)
                else:
                    if display_name and (not user.first_name or user.first_name == "Player"):
                        user.first_name = display_name
                    if kwargs.get("username") and user.username != kwargs["username"]:
                        user.username = kwargs["username"]
                user.matches_played = (user.matches_played or 0) + 1
                if is_win:
                    user.matches_won = (user.matches_won or 0) + 1
                user.total_runs = (user.total_runs or 0) + runs
                user.total_wickets = (user.total_wickets or 0) + wickets
                user.total_sixes = (user.total_sixes or 0) + sixes
                user.total_fours = (user.total_fours or 0) + fours
                user.total_ducks = (user.total_ducks or 0) + ducks
                user.balls_faced = (user.balls_faced or 0) + balls_faced
                user.balls_bowled = (user.balls_bowled or 0) + balls_bowled
                user.runs_conceded = (user.runs_conceded or 0) + runs_conceded
                if mode == "team":
                    user.team_matches = (user.team_matches or 0) + 1
                else:
                    user.solo_matches = (user.solo_matches or 0) + 1
                if runs > (user.highest_score or 0):
                    user.highest_score = runs
                if award_diamonds:
                    user.diamonds = (user.diamonds or 0) + (runs * 2) + (wickets * 10) + (50 if is_win else 10)
                if match_id:
                    session.add(MatchPlayerRecord(
                        match_id=match_id,
                        user_id=user_id,
                        display_name=display_name or user.first_name or "Player",
                        mode=mode,
                        runs=runs,
                        wickets=wickets,
                        fours=fours,
                        sixes=sixes,
                        ducks=ducks,
                        balls_faced=balls_faced,
                        balls_bowled=balls_bowled,
                        runs_conceded=runs_conceded,
                        is_win=is_win,
                    ))
                await session.commit()
                return True
        except Exception:
            logger.exception("Could not record match statistics for user %s", user_id)
            return False

economy_service = EconomyService()
