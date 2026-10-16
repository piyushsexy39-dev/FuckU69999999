"""Plain-sqlite helpers (standard library only) used by the database backup feature."""
import hashlib
import os
import sqlite3
from typing import Optional, Tuple


def sqlite_path_from_url(url: str) -> Optional[str]:
    """Absolute path of the sqlite file in a SQLAlchemy URL, or None for other databases."""
    if url.startswith("sqlite") and ":///" in url:
        return os.path.abspath(url.split(":///", 1)[1])
    return None


def count_users(path: str) -> int:
    """Number of rows in the users table (0 if the file/table does not exist)."""
    if not os.path.exists(path):
        return 0
    try:
        conn = sqlite3.connect(path, timeout=10)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def snapshot(path: str, dest: str) -> None:
    """Consistent copy of a live (possibly WAL-mode) sqlite database."""
    src = sqlite3.connect(path, timeout=30)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def validate_backup(path: str) -> Tuple[bool, str, int]:
    """Check that a file is a healthy Ultimate Cricket database. Returns (ok, message, users)."""
    try:
        with open(path, "rb") as handle:
            if handle.read(16) != b"SQLite format 3\x00":
                return False, "This is not a database file.", 0
        conn = sqlite3.connect(path, timeout=10)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return False, "The database file is damaged.", 0
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "users" not in tables:
                return False, "This database has no players table, so it is not from this bot.", 0
            users = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        finally:
            conn.close()
        return True, "ok", users
    except (sqlite3.Error, OSError) as error:
        return False, f"Could not read the file: {error}", 0


def file_digest(path: str) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()
