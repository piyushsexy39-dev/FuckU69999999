import os
import json
from typing import Dict, Optional

CACHE_FILE = os.path.join(
    "/app/applet" if os.path.exists("/app/applet") else os.path.dirname(os.path.abspath(__file__)),
    "telegram_media_cache.json"
)

class MediaCache:
    def __init__(self):
        self._cache: Dict[str, str] = {}
        self._load()

    def _load(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._cache.update(data)
            except Exception:
                pass

    def _save(self):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._cache, f)
        except Exception:
            pass

    def get(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def set(self, key: str, value: Optional[str]):
        if value is None:
            self._cache.pop(key, None)
        else:
            self._cache[key] = str(value)
        self._save()

    def clear(self):
        self._cache.clear()
        self._save()

media_cache = MediaCache()
