"""HTTP server: login, static pages, and a read-only JSON API. Standard library only."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
from datetime import datetime, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .alerts import OpsSettings, VoiceOps
from .auth import LoginLimiter, Sessions, UserStore
from .settings import Settings
from .voice import VoiceTestSource, public_call
from .voice_calls import VoiceCallSource
from .voice_calls import public_call as public_voice_call
from .whatsapp import OUTCOMES, AlertSettings, WhatsAppSource

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
STATIC_FILES = {"app.css", "app.js", "login.css", "login.js"}
COOKIE = "evdash"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RANGES = {"7": 7, "30": 30, "90": 90, "all": None}

SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",  # "no-referrer" makes browsers send Origin: null on our own form posts
    "Cache-Control": "no-store",
}


class App:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.users = UserStore(settings.users_file)
        self.sessions = Sessions(settings.secret_key, settings.session_hours)
        self.limiter = LoginLimiter()
        self.whatsapp = WhatsAppSource(settings.evproto_logs_dir, settings.tz, AlertSettings(
            error_rate=settings.wa_alert_error_rate, unanswered_rate=settings.wa_alert_unanswered_rate,
            wait_seconds=settings.wa_alert_wait_seconds, quiet_hours=settings.wa_alert_quiet_hours,
            failed_logins=settings.wa_alert_failed_logins))
        self.voice = VoiceTestSource(settings.voice_runs_dir, settings.tz, settings.voice_log_tz)
        self.voice_calls = VoiceCallSource(settings.voice_agent_data_dir, settings.tz, settings.voice_cache_seconds, costs={
            "gpu_month": settings.voice_gpu_cost_aed_month, "other_month": settings.voice_other_cost_aed_month,
            "line_minute": settings.voice_line_cost_aed_min})
        self.voice_ops = VoiceOps(self.voice_calls, OpsSettings(
            max_calls_per_day=settings.voice_max_calls_per_day, max_calls_per_week=settings.voice_max_calls_per_week,
            retention_days=settings.voice_retention_days, alert_error_rate=settings.voice_alert_error_rate,
            alert_wait_seconds=settings.voice_alert_wait_seconds, alert_quiet_hours=settings.voice_alert_quiet_hours))
        self._log_lock = threading.Lock()

    def access_log(self, event: str, **fields) -> None:
        """Who logged in, and who opened the text of a question or a call. Never breaks a request."""
        path = self.settings.access_log
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **fields}
            with self._log_lock, open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "EVAgentDashboard"
        sys_version = ""

        # ---------- helpers ----------
        def log_message(self, fmt, *args):  # keep request lines out of stderr noise, errors still show
            pass

        def client_ip(self) -> str:
            if app.settings.trust_proxy:
                fwd = self.headers.get("X-Forwarded-For", "")
                if fwd:
                    return fwd.split(",")[-1].strip()  # the address our own proxy saw
            return self.client_address[0]

        def session(self) -> Optional[dict]:
            jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return app.sessions.read(jar[COOKIE].value if COOKIE in jar else None)

        def send(self, status: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
            self.send_response(status)
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json(self, status: int, data) -> None:
            self.send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def redirect(self, location: str, extra: Optional[dict] = None) -> None:
            self.send(303, b"", "text/plain", {"Location": location, **(extra or {})})

        def file(self, name: str) -> None:
            path = os.path.join(STATIC_DIR, name)
            if not os.path.isfile(path):
                return self.send(404, b"Not found", "text/plain")
            with open(path, "rb") as f:
                body = f.read()
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self.send(200, body, ctype)

        def cookie(self, value: str, max_age: int) -> str:
            parts = [f"{COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
            if app.settings.cookie_secure:
                parts.append("Secure")
            return "; ".join(parts)

        def same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urlparse(origin).netloc == self.headers.get("Host", "")

        # ---------- routes ----------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            path, qs = url.path, {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if path == "/login":
                    return self.file("login.html")
                if path.startswith("/static/") and path[len("/static/"):] in STATIC_FILES:
                    return self.file(path[len("/static/"):])
                if path == "/healthz":
                    return self.json(200, {"ok": True})
                user = self.session()
                if path == "/":
                    return self.file("index.html") if user else self.redirect("/login")
                if path.startswith("/api/"):
                    if not user:
                        return self.json(401, {"error": "Please sign in again."})
                    return self.api(path, qs, user)
                return self.send(404, b"Not found", "text/plain")
            except Exception as exc:  # one bad file must not take the dashboard down
                app.access_log("server_error", path=path, error=type(exc).__name__)
                return self.json(500, {"error": "Something went wrong reading the data. Check the server log."})

        def do_POST(self):
            path = urlparse(self.path).path
            if not self.same_origin():
                return self.send(403, b"Forbidden", "text/plain")
            if path == "/logout":
                return self.redirect("/login", {"Set-Cookie": self.cookie("", 0)})
            if path != "/login":
                return self.send(404, b"Not found", "text/plain")
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            form = {k: v[-1] for k, v in parse_qs(self.rfile.read(length).decode("utf-8", "replace")).items()}
            username, password, ip = form.get("username", ""), form.get("password", ""), self.client_ip()
            if app.limiter.locked(username, ip):
                app.access_log("login_locked", username=username, ip=ip)
                return self.redirect("/login?error=locked")
            user = app.users.authenticate(username, password)
            if not user:
                app.limiter.failed(username, ip)
                app.access_log("login_failed", username=username, ip=ip)
                return self.redirect("/login?error=wrong")
            app.limiter.succeeded(username, ip)
            app.access_log("login", username=user["username"], role=user["role"], ip=ip)
            token = app.sessions.issue(user)
            return self.redirect("/", {"Set-Cookie": self.cookie(token, app.settings.session_hours * 3600)})

        # ---------- API ----------
        def api(self, path: str, qs: dict, user: dict):
            admin = user["role"] == "admin"
            days = RANGES.get(qs.get("range", "30"), 30)
            if path == "/api/me":
                return self.json(200, {"name": user["name"], "username": user["username"], "role": user["role"],
                                       "outcomes": OUTCOMES})
            if path == "/api/status":
                return self.json(200, {"whatsapp": app.whatsapp.status(), "voice": app.voice.status(),
                                       "voice_calls": app.voice_calls.status()})
            if path == "/api/overview":
                wa = app.whatsapp.summary(days)
                vo = app.voice.summary(days)
                return self.json(200, {"range": qs.get("range", "30"), "whatsapp": wa, "voice": vo,
                                       "voice_calls": app.voice_calls.status(),
                                       "voice_calls_per_day": app.voice_calls.per_day(days)})
            if path == "/api/voicecalls/summary":
                return self.json(200, app.voice_calls.summary(days))
            if path == "/api/voicecalls/hours":
                day = qs.get("day", "")
                if day and not _DAY.match(day):
                    return self.json(400, {"error": "day must look like 2026-10-19."})
                return self.json(200, app.voice_calls.hours(days, day or None))
            if path == "/api/voicecalls/clients":
                try:
                    offset, min_calls = max(0, int(qs.get("offset", "0"))), max(0, int(qs.get("min_calls", "0")))
                except ValueError:
                    return self.json(400, {"error": "offset and min_calls must be numbers."})
                return self.json(200, app.voice_calls.clients(days, q=qs.get("q", "")[:60], min_calls=min_calls,
                                                              sort=qs.get("sort", "calls"), offset=offset,
                                                              show_names=admin))
            if path == "/api/alerts":
                return self.json(200, app.voice_ops.alerts())
            if path == "/api/voicecalls/compliance":
                ops = app.voice_ops
                return self.json(200, {"blocked": ops.blocked(days), "compliance": ops.compliance(days),
                                       "retention": ops.retention(), "health": ops.health()})
            if path == "/api/voicecalls/followup":
                return self.json(200, app.voice_calls.follow_up(show_text=admin))
            if path == "/api/voicecalls/client":
                client = app.voice_calls.client_calls(qs.get("id", ""), show_names=admin)
                if not client:
                    return self.json(404, {"error": "No calls found for that client."})
                return self.json(200, client)
            if path == "/api/voicecalls/call":
                call = app.voice_calls.call(qs.get("id", ""))
                if not call:
                    return self.json(404, {"error": "That call was not found."})
                if admin:
                    app.access_log("view_voice_call", username=user["username"], call_id=call["id"])
                return self.json(200, public_voice_call(call, full=True, show_text=admin))
            if path == "/api/speech/run":
                run = app.voice_calls.speech_run(qs.get("id", ""), show_text=admin)
                return self.json(200, run) if run else self.json(404, {"error": "That speech test was not found."})
            if path == "/api/whatsapp/summary":
                return self.json(200, app.whatsapp.summary(days))
            if path == "/api/whatsapp/questions":
                return self.json(200, app.whatsapp.question_list(
                    days, show_text=admin, outcome=qs.get("outcome", ""), flag=qs.get("flag", ""),
                    q=qs.get("q", ""), limit=min(int(qs.get("limit", "50") or 50), 200),
                    offset=max(int(qs.get("offset", "0") or 0), 0)))
            if path == "/api/whatsapp/question":
                try:
                    qid = int(qs.get("id", ""))
                except ValueError:
                    return self.json(400, {"error": "Missing question id."})
                item = app.whatsapp.question(qid, show_text=admin)
                if not item:
                    return self.json(404, {"error": "That question was not found."})
                if admin:
                    app.access_log("view_question", username=user["username"], question_id=qid)
                    item["steps"] = app.whatsapp.steps(qid)
                return self.json(200, item)
            if path == "/api/whatsapp/alerts":
                return self.json(200, app.whatsapp.alerts())
            if path == "/api/whatsapp/errors":
                return self.json(200, app.whatsapp.error_summary(days))
            if path == "/api/whatsapp/errorlist":
                try:
                    offset = max(int(qs.get("offset", "0") or 0), 0)
                except ValueError:
                    return self.json(400, {"error": "offset must be a number."})
                return self.json(200, app.whatsapp.error_list(days, show_text=admin, kind=qs.get("kind", "")[:40],
                                                              offset=offset))
            if path == "/api/whatsapp/error":
                try:
                    eid = int(qs.get("id", ""))
                except ValueError:
                    return self.json(400, {"error": "Missing error id."})
                item = app.whatsapp.error(eid, show_text=admin)
                if not item:
                    return self.json(404, {"error": "That error was not found."})
                if admin:
                    app.access_log("view_error", username=user["username"], error_id=eid)
                return self.json(200, item)
            if path == "/api/whatsapp/logins":
                if not admin:
                    return self.json(403, {"error": "Only admins can see the login log."})
                return self.json(200, app.whatsapp.login_summary(days))
            if path == "/api/voice/runs":
                return self.json(200, {"runs": app.voice.runs()})
            if path == "/api/voice/run":
                detail = app.voice.run_detail(qs.get("id", ""))
                return self.json(200, detail) if detail else self.json(404, {"error": "That test run was not found."})
            if path == "/api/voice/call":
                call = app.voice.call(qs.get("id", ""))
                if not call:
                    return self.json(404, {"error": "That call was not found."})
                if admin:
                    app.access_log("view_call", username=user["username"], call_id=call["id"])
                return self.json(200, public_call(call, with_events=True, show_text=admin))
            return self.json(404, {"error": "Unknown address."})

    return Handler


def serve(settings: Settings) -> ThreadingHTTPServer:
    app = App(settings)
    httpd = ThreadingHTTPServer((settings.host, settings.port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
