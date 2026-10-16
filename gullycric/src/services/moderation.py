import asyncio
import logging
from typing import Dict, Optional

from sqlalchemy import select

from ..config import config
from ..db.models import BannedUser, Moderator
from ..db.session import async_session_factory

logger = logging.getLogger("gullycric.moderation")


class ModerationService:
    """Moderators and banned players, kept in memory and saved in the database."""

    def __init__(self):
        self.mods: Dict[int, dict] = {}
        self.banned: Dict[int, dict] = {}
        self.loaded = False
        self._lock = asyncio.Lock()

    async def ensure_loaded(self):
        if self.loaded:
            return
        async with self._lock:
            if self.loaded:
                return
            async with async_session_factory() as db:
                for row in (await db.execute(select(Moderator))).scalars().all():
                    self.mods[row.user_id] = {"username": row.username, "name": row.first_name}
                for row in (await db.execute(select(BannedUser))).scalars().all():
                    self.banned[row.user_id] = {"username": row.username, "name": row.first_name}
            self.loaded = True

    @staticmethod
    def is_owner(user_id: Optional[int]) -> bool:
        return user_id in config.owner_ids

    def is_mod(self, user_id: Optional[int]) -> bool:
        return user_id in self.mods

    def is_staff(self, user_id: Optional[int]) -> bool:
        return self.is_owner(user_id) or self.is_mod(user_id)

    def is_banned(self, user_id: Optional[int]) -> bool:
        return user_id in self.banned

    async def add_mod(self, user_id: int, name: str, username: Optional[str], added_by: int):
        async with async_session_factory() as db:
            row = await db.get(Moderator, user_id)
            if row is None:
                db.add(Moderator(user_id=user_id, username=username, first_name=name, added_by=added_by))
            else:
                row.username, row.first_name = username, name
            await db.commit()
        self.mods[user_id] = {"username": username, "name": name}

    async def remove_mod(self, user_id: int) -> bool:
        async with async_session_factory() as db:
            row = await db.get(Moderator, user_id)
            if row is not None:
                await db.delete(row)
                await db.commit()
        return self.mods.pop(user_id, None) is not None

    async def ban(self, user_id: int, name: str, username: Optional[str], banned_by: int):
        async with async_session_factory() as db:
            row = await db.get(BannedUser, user_id)
            if row is None:
                db.add(BannedUser(user_id=user_id, username=username, first_name=name, banned_by=banned_by))
            else:
                row.username, row.first_name = username, name
            await db.commit()
        self.banned[user_id] = {"username": username, "name": name}

    async def unban(self, user_id: int) -> bool:
        async with async_session_factory() as db:
            row = await db.get(BannedUser, user_id)
            if row is not None:
                await db.delete(row)
                await db.commit()
        return self.banned.pop(user_id, None) is not None


moderation_service = ModerationService()
