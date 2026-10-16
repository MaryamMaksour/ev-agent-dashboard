"""All settings in one place. Read from environment variables, or from a .env file next to server.py."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone

USD_TO_AED = 3.6725          # USD -> AED peg
SLOW_ANSWER_SECONDS = 10     # a WhatsApp answer slower than this counts as "slow"
REPHRASE_WINDOW_SECONDS = 180  # same person, similar question within this window = "asked again"


def load_dotenv(path: str) -> None:
    """Minimal .env reader: KEY=VALUE lines, # comments. Real environment variables win."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    evproto_logs_dir: str
    voice_runs_dir: str
    secret_key: str = ""
    host: str = "127.0.0.1"
    port: int = 8585
    users_file: str = "users.json"
    access_log: str = "logs/dashboard_access.jsonl"
    cookie_secure: bool = False
    trust_proxy: bool = False
    session_hours: int = 8
    tz_offset_hours: float = 4.0          # UAE time for charts (no daylight saving)
    voice_log_tz_offset_hours: float = 4.0  # voice test logs are written in server local time, without a zone

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            evproto_logs_dir=e("EVPROTO_LOGS_DIR", "../me/ev-proto/logs"),
            voice_runs_dir=e("VOICE_RUNS_DIR", "../voice-agent-llm-test/runs"),
            secret_key=e("DASHBOARD_SECRET_KEY", ""),
            host=e("DASHBOARD_HOST", "127.0.0.1"),
            port=int(e("DASHBOARD_PORT", "8585")),
            users_file=e("DASHBOARD_USERS_FILE", "users.json"),
            access_log=e("DASHBOARD_ACCESS_LOG", "logs/dashboard_access.jsonl"),
            cookie_secure=_bool(e("DASHBOARD_COOKIE_SECURE", "0")),
            trust_proxy=_bool(e("DASHBOARD_TRUST_PROXY", "0")),
            session_hours=int(e("DASHBOARD_SESSION_HOURS", "8")),
            tz_offset_hours=float(e("DASHBOARD_UTC_OFFSET_HOURS", "4")),
            voice_log_tz_offset_hours=float(e("VOICE_LOG_UTC_OFFSET_HOURS", "4")),
        )

    @property
    def tz(self) -> timezone:
        return timezone(timedelta(hours=self.tz_offset_hours))

    @property
    def voice_log_tz(self) -> timezone:
        return timezone(timedelta(hours=self.voice_log_tz_offset_hours))
