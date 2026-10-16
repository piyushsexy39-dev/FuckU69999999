import os
import shutil
import datetime
import json
import hmac
import hashlib
from typing import Dict, Any
try:
    from ..config import config
except ImportError:
    from config import config

class BackupService:
    @staticmethod
    def create_database_backup(db_path: str = "gullycric.db", backup_dir: str = "backups") -> str:
        if not os.path.exists(db_path):
            return ""
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"ultimate_cricket_{timestamp}.bak")
        shutil.copy2(db_path, backup_path)
        return backup_path

    @staticmethod
    def generate_signed_backup(session_dict: Dict[str, Any]) -> bytes:
        payload = dict(session_dict)
        payload.pop("signature", None)
        payload["schema_version"] = 1
        
        canonical_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        secret = config.backup_signing_secret
        signature = hmac.new(
            secret.encode("utf-8"),
            canonical_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        
        payload["signature"] = signature
        return json.dumps(payload, indent=2).encode("utf-8")

    @staticmethod
    def verify_and_load_backup(file_bytes: bytes) -> Dict[str, Any]:
        try:
            data = json.loads(file_bytes.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Malformed JSON backup file: {e}")
        
        if not isinstance(data, dict):
            raise ValueError("Invalid backup structure: expected JSON object.")
        
        signature = data.get("signature")
        if not signature:
            raise ValueError("Missing cryptographic signature in backup file. Unsigned backups are rejected.")
        
        payload = dict(data)
        payload.pop("signature", None)
        
        canonical_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        secret = config.backup_signing_secret
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            canonical_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(str(signature), expected_signature):
            raise ValueError("Cryptographic signature verification failed: file is tampered, forged, or signed with a different key.")
        
        return data

backup_service = BackupService()
