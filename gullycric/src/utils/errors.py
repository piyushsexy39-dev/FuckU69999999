class DatabaseError(Exception):
    """Base exception for database operations."""
    pass

class UserNotFoundError(DatabaseError):
    pass

class MatchNotFoundError(DatabaseError):
    pass

class ConcurrencyError(DatabaseError):
    pass
