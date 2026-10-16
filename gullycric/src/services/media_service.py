import os
import io
from typing import Optional, Dict
from ..config import config
from .media_cache import media_cache

ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets",
    "media",
)
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))

class MediaService:
    def __init__(self):
        self.enabled = config.enable_media

    def get_local_media_path(self, event_type: str) -> Optional[str]:
        """Returns path to downloaded MP4/JPG asset if available."""
        if not self.enabled:
            return None
        mapping = {
            "wicket": "wicket.mp4",
            "duck": "duck.mp4",
            "shot_1": "shot_1.mp4",
            "shot_5": "shot_5.mp4",
            "six": "six.mp4",
            "four": "four.mp4",
            "single": "single.mp4",
            "double": "double.mp4",
            "dot": "dot.mp4",
            "pulse": "pulse.mp4",
            "batting": "batting.mp4",
            "openers": "openers.mp4",
            "crease_entry": "crease_entry.mp4",
            "toss": "toss.mp4",
            "start_banner": "start_banner.jpg",
            "scorecard": "scorecard.jpg",
            "potm": "potm.jpg",
        }
        fname = mapping.get(event_type)
        if not fname:
            return None
        candidates = [
            "/app/applet",
            os.getcwd(),
            ASSETS_DIR,
            MODULE_DIR,
            "/tmp/gullycric_root/gullycric/assets/media",
        ]
        for media_dir in candidates:
            if not media_dir:
                continue
            path = os.path.join(media_dir, fname)
            if os.path.isfile(path):
                return path
        return None

    def get_event_media_url(self, event_type: str) -> Optional[str]:
        cached = media_cache.get(event_type)
        if cached:
            return cached
        path = self.get_local_media_path(event_type)
        url = f"file://{path}" if path else f"https://example.com/{event_type}.mp4"
        media_cache.set(event_type, url)
        return url

    def generate_scorecard_image(
        self,
        batting_name: str,
        bowling_name: str,
        total_runs: int,
        wickets: int,
        overs: str,
        target: int,
        summary: str
    ) -> bytes:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        img = Image.new("RGB", (600, 350), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, 600, 60], fill=(30, 41, 59))
        draw.text((20, 18), "🏏 ULTIMATE CRICKET OFFICIAL SCOREBOARD", fill=(255, 255, 255))
        y = 80
        draw.text((30, y), f"Batting: {batting_name}", fill=(226, 232, 240))
        y += 35
        draw.text((30, y), f"Bowling: {bowling_name}", fill=(226, 232, 240))
        y += 35
        draw.text((30, y), f"Score: {total_runs}/{wickets} ({overs} Ov)", fill=(52, 211, 153))
        y += 35
        draw.text((30, y), f"Target: {target}", fill=(244, 63, 94))
        y += 45
        draw.rectangle([20, y, 580, y + 2], fill=(71, 85, 105))
        y += 15
        draw.text((30, y), summary, fill=(250, 204, 21))
        bio = io.BytesIO()
        img.save(bio, format="PNG")
        return bio.getvalue()

media_service = MediaService()
