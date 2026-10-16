import os
os.environ.setdefault("OWNER_IDS", "8445198778")
import pytest
import pytest_asyncio
from sqlalchemy import delete
from gullycric.src.db.session import init_db, async_session_factory
from gullycric.src.db.models import RewardLedger, User

@pytest_asyncio.fixture(autouse=True, scope="function")
async def setup_database():
    await init_db()
    try:
        async with async_session_factory() as session:
            await session.execute(delete(RewardLedger).where(RewardLedger.idempotency_key == "match_win_555_123"))
            await session.execute(delete(User).where(User.user_id == 555))
            await session.commit()
    except Exception:
        pass
