import os
from dataclasses import dataclass, field
from typing import List

# Load environment variables safely
def _load_env_file():
    try:
        from dotenv import load_dotenv
        for p in [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"), ".env"]:
            if os.path.exists(p):
                load_dotenv(p, override=False)
                break
        else:
            load_dotenv(override=False)
    except Exception:
        pass
    

_load_env_file()

def _resolve_backup_secret() -> str:
    """BACKUP_SIGNING_SECRET from env, else a random secret generated once and
    stored next to the database (so it survives restarts)."""
    val = os.getenv("BACKUP_SIGNING_SECRET", "").strip()
    if val:
        return val
    import secrets
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///gullycric.db")
    base = os.getcwd()
    if ":///" in url and url.startswith("sqlite"):
        db_path = url.split(":///", 1)[1]
        base = os.path.dirname(os.path.abspath(db_path)) or base
    path = os.path.join(base, ".backup_secret")
    try:
        with open(path) as f:
            saved = f.read().strip()
        if saved:
            return saved
    except OSError:
        pass
    val = secrets.token_hex(32)
    try:
        with open(path, "w") as f:
            f.write(val)
        os.chmod(path, 0o600)
    except OSError:
        pass
    return val

@dataclass
class Config:
    bot_token: str = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///gullycric.db")
    owner_ids: List[int] = field(default_factory=lambda: [
        int(x.strip()) for x in os.getenv("OWNER_IDS", "8445198778").split(",") if x.strip().isdigit()
    ])
    # IMPORTANT: Part 3 - 3-Ball Default Behavior as observed in screen recording
    default_solo_balls_per_over: int = int(os.getenv("DEFAULT_SOLO_BALLS_PER_OVER", "3"))
    default_solo_overs: int = int(os.getenv("DEFAULT_SOLO_OVERS", "1"))
    
    # Team match defaults
    default_team_balls_per_over: int = int(os.getenv("DEFAULT_TEAM_BALLS_PER_OVER", "6"))
    default_team_overs: int = int(os.getenv("DEFAULT_TEAM_OVERS", "2"))
    
    # Timers
    afk_timeout_seconds: int = int(os.getenv("AFK_TIMEOUT_SECONDS", "50"))
    lobby_timeout_seconds: int = int(os.getenv("LOBBY_TIMEOUT_SECONDS", "120"))
    
    # Media
    enable_media: bool = os.getenv("ENABLE_MEDIA", "true").lower() in ("true", "1", "yes")
    media_base_url: str = os.getenv("MEDIA_BASE_URL", "")
    backup_signing_secret: str = field(default_factory=lambda: _resolve_backup_secret())

    def owner_contact_url(self) -> str:
        return f"tg://user?id={self.owner_ids[0]}" if self.owner_ids else "https://t.me/"

config = Config()
