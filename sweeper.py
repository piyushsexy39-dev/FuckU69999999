import asyncio
import time
import logging
from .services.match_service import match_service
from .config import config

logger = logging.getLogger("gullycric.sweeper")

class MatchSweeper:
    def __init__(self, interval_seconds: int = 15):
        self.interval = interval_seconds
        self.is_running = False
        self.bot = None

    async def _tidy_lobby(self, session, text):
        if not self.bot or not getattr(session, "lobby_message_id", None):
            return
        try:
            from types import SimpleNamespace
            from .handlers import solo
            await solo.close_lobby_message(SimpleNamespace(bot=self.bot), session, text)
        except Exception as e:
            logger.warning(f"Could not tidy lobby message: {e}")

    async def run_loop(self):
        self.is_running = True
        logger.info("Match sweeper started.")
        while self.is_running:
            try:
                now = time.time()
                # Check for timed out lobbies
                for s in list(match_service.active_sessions.values()):
                    if s.status == "lobby" and now - s.last_action_timestamp > config.lobby_timeout_seconds:
                        s.status = "cancelled"
                        logger.info(f"Swept expired lobby: {s.match_id}")
                        await self._tidy_lobby(s, "⌛ <b>SOLO LOBBY EXPIRED</b>\nNobody started the match in time.\nStart a new one with /solo")
                    elif s.status == "active" and now - s.last_action_timestamp > config.afk_timeout_seconds * 3:
                        s.status = "cancelled"
                        logger.info(f"Swept abandoned active match: {s.match_id}")
                        await self._tidy_lobby(s, "💤 <b>SOLO MATCH ABANDONED</b>\nNo activity for too long.\nStart a new one with /solo")
            except Exception as e:
                logger.error(f"Error in match sweeper: {e}")
            await asyncio.sleep(self.interval)

    def stop(self):
        self.is_running = False

sweeper = MatchSweeper()
