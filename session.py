import os
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .base import Base
from ..config import config

engine = create_async_engine(
    config.database_url,
    echo=False,
    future=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def init_db():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("PRAGMA journal_mode=WAL;"))
            await conn.execute(text("PRAGMA synchronous=NORMAL;"))
            await conn.execute(text("PRAGMA cache_size=10000;"))
            await conn.execute(text("PRAGMA temp_store=MEMORY;"))
            await conn.execute(text("PRAGMA busy_timeout=5000;"))
        except Exception:
            pass
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_user_statistics)


def _add_missing_user_statistics(connection):
    columns = {
        column["name"]
        for column in inspect(connection).get_columns("users")
    }
    additions = {
        "total_ducks": "INTEGER NOT NULL DEFAULT 0",
        "balls_faced": "INTEGER NOT NULL DEFAULT 0",
        "balls_bowled": "INTEGER NOT NULL DEFAULT 0",
        "runs_conceded": "INTEGER NOT NULL DEFAULT 0",
        "solo_matches": "INTEGER NOT NULL DEFAULT 0",
        "team_matches": "INTEGER NOT NULL DEFAULT 0",
        "started_bot": "BOOLEAN NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))

async def get_db():
    async with async_session_factory() as session:
        yield session
