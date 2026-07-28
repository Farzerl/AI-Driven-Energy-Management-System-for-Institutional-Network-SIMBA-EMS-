from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from threading import RLock
from typing import Mapping

DEFAULT_ADMIN_PASSWORD = "admin"
PBKDF2_ITERATIONS = 310_000
SESSION_SECONDS = 60 * 60


def _hash_password(password: str, salt: bytes) -> str:
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return base64.b64encode(derived).decode("ascii")


class AdminAuthStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._sessions: dict[str, float] = {}
        self._record = self._load_or_create()

    def _load_or_create(self) -> dict[str, object]:
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(value, Mapping) and value.get("salt") and value.get("password_hash"):
                    return dict(value)
            except Exception:
                pass
        salt = secrets.token_bytes(16)
        value = {
            "schema_version": 1,
            "salt": base64.b64encode(salt).decode("ascii"),
            "password_hash": _hash_password(DEFAULT_ADMIN_PASSWORD, salt),
            "must_change_password": True,
        }
        self._atomic_write(value)
        return value

    def _atomic_write(self, value: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)

    def login(self, password: str) -> dict[str, object]:
        candidate = str(password)
        if not candidate:
            raise ValueError("Admin password is required.")
        with self._lock:
            salt = base64.b64decode(str(self._record["salt"]))
            expected = str(self._record["password_hash"])
            if not hmac.compare_digest(_hash_password(candidate, salt), expected):
                raise ValueError("The admin password is incorrect.")
            token = secrets.token_urlsafe(32)
            expires_at = time.time() + SESSION_SECONDS
            self._sessions[token] = expires_at
            self._purge()
            return {
                "token": token,
                "expires_in_seconds": SESSION_SECONDS,
                "must_change_password": bool(self._record.get("must_change_password", False)),
            }

    def _purge(self) -> None:
        now = time.time()
        self._sessions = {token: expiry for token, expiry in self._sessions.items() if expiry > now}

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            self._purge()
            return self._sessions.get(token, 0.0) > time.time()

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def change_password(self, token: str | None, current_password: str, new_password: str) -> dict[str, object]:
        if not self.validate(token):
            raise PermissionError("A valid admin session is required.")
        if len(new_password) < 8:
            raise ValueError("The new admin password must contain at least 8 characters.")
        if new_password == DEFAULT_ADMIN_PASSWORD:
            raise ValueError("Choose a password different from the temporary default.")
        with self._lock:
            salt = base64.b64decode(str(self._record["salt"]))
            if not hmac.compare_digest(
                _hash_password(current_password, salt),
                str(self._record["password_hash"]),
            ):
                raise ValueError("The current admin password is incorrect.")
            new_salt = secrets.token_bytes(16)
            self._record = {
                "schema_version": 1,
                "salt": base64.b64encode(new_salt).decode("ascii"),
                "password_hash": _hash_password(new_password, new_salt),
                "must_change_password": False,
            }
            self._sessions.clear()
            self._atomic_write(self._record)
            return {"status": "changed", "sessions_revoked": True}

    def status(self) -> dict[str, object]:
        with self._lock:
            self._purge()
            return {
                "configured": True,
                "must_change_password": bool(self._record.get("must_change_password", False)),
                "active_sessions": len(self._sessions),
                "session_timeout_seconds": SESSION_SECONDS,
            }
