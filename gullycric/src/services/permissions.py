from typing import List, Any
from ..config import config

class PermissionService:
    @staticmethod
    def is_owner(user_id: int) -> bool:
        return user_id in config.owner_ids

    @staticmethod
    def is_admin(user_id: int) -> bool:
        return user_id in config.owner_ids

    @staticmethod
    async def is_chat_admin(bot: Any, chat_id: int, user_id: int) -> bool:
        """Checks if user is bot owner or group administrator/creator."""
        if user_id in config.owner_ids:
            return True
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            return member.status in ("creator", "administrator")
        except Exception:
            return False

    @staticmethod
    def can_force_start(user_id: int, host_id: int) -> bool:
        """Only match host or bot admin/owner can force start."""
        return user_id == host_id or user_id in config.owner_ids

    @staticmethod
    def can_cancel_match(user_id: int, host_id: int) -> bool:
        return user_id == host_id or user_id in config.owner_ids

permission_service = PermissionService()
