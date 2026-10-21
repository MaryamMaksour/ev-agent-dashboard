"""Login for the dashboard: users file with hashed passwords, signed session cookies, and a lockout after
repeated failures. Standard library only.

Everyone who can log in is an admin and sees everything, including question/answer text, transcripts,
IPs and the login log. There are no other roles.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Optional

ROLE = "admin"
_ITERATIONS = 240_000


def hash_password(password: str, salt: Optional[bytes] = None, iterations: int = _ITERATIONS) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt), int(iterations))
        return hmac.compare_digest(check, _unb64(digest))
    except (ValueError, TypeError):
        return False


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class UserStore:
    """users.json: {"maryam": {"name": "Maryam", "role": "admin", "password": "<hash>"}}. Re-read when it changes."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._users: dict = {}
        self._mtime: Optional[float] = None
        self._lock = threading.Lock()

    def _reload(self) -> None:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            self._users, self._mtime = {}, None
            return
        if mtime != self._mtime:
            with open(self.path, encoding="utf-8") as f:
                self._users = json.load(f)
            self._mtime = mtime

    def all(self) -> dict:
        with self._lock:
            self._reload()
            return dict(self._users)

    def get(self, username: str) -> Optional[dict]:
        return self.all().get(username.strip().lower())

    def authenticate(self, username: str, password: str) -> Optional[dict]:
        user = self.get(username or "")
        if not user:
            hash_password(password or "", b"0" * 16)  # same work either way, so timing doesn't reveal usernames
            return None
        if not verify_password(password or "", user.get("password", "")):
            return None
        if user.get("role", ROLE) != ROLE:
            return None  # an old entry from when a "viewer" role existed: it must be re-added as an admin
        return {"username": username.strip().lower(), "name": user.get("name") or username, "role": ROLE}

    def save(self, users: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


class Sessions:
    """Session cookie = base64(json payload) + "." + HMAC-SHA256 signature. Nothing is stored server-side."""

    def __init__(self, secret: str, hours: int) -> None:
        if len(secret) < 32:
            raise ValueError("DASHBOARD_SECRET_KEY must be at least 32 characters")
        self.key = secret.encode("utf-8")
        self.ttl = hours * 3600

    def issue(self, user: dict, now: Optional[float] = None) -> str:
        now = now or time.time()
        payload = {"u": user["username"], "n": user["name"], "r": user["role"], "exp": int(now + self.ttl)}
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}"

    def read(self, token: Optional[str], now: Optional[float] = None) -> Optional[dict]:
        if not token or "." not in token:
            return None
        body, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(body)):
            return None
        try:
            payload = json.loads(_unb64(body))
        except ValueError:
            return None
        if payload.get("exp", 0) < (now or time.time()) or payload.get("r") != ROLE:
            return None
        return {"username": payload["u"], "name": payload["n"], "role": payload["r"]}

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())


class LoginLimiter:
    """5 failed attempts for the same username or the same IP within 15 minutes -> locked for 15 minutes."""

    def __init__(self, max_fails: int = 5, window: int = 900, lock_for: int = 900) -> None:
        self.max_fails, self.window, self.lock_for = max_fails, window, lock_for
        self._fails: dict = {}
        self._locked: dict = {}
        self._lock = threading.Lock()

    def _keys(self, username: str, ip: str) -> list:
        return [f"u:{(username or '').strip().lower()}", f"ip:{ip}"]

    def locked(self, username: str, ip: str, now: Optional[float] = None) -> bool:
        now = now or time.time()
        with self._lock:
            return any(self._locked.get(k, 0) > now for k in self._keys(username, ip))

    def failed(self, username: str, ip: str, now: Optional[float] = None) -> None:
        now = now or time.time()
        with self._lock:
            for k in self._keys(username, ip):
                recent = [t for t in self._fails.get(k, []) if now - t < self.window] + [now]
                self._fails[k] = recent
                if len(recent) >= self.max_fails:
                    self._locked[k] = now + self.lock_for
                    self._fails[k] = []

    def succeeded(self, username: str, ip: str) -> None:
        with self._lock:
            self._fails.pop(self._keys(username, ip)[0], None)
