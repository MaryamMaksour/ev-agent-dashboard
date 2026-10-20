"""Reads what the WhatsApp data agent (ev-proto) already saves. READ-ONLY: never writes to its files.

  logs/audit.db          qa_log    - one row per answered question (user, question, answer, sources, model, cost, time)
                         auth_log  - one row per login attempt
                         error_log - one row per error while serving someone (added with the error logging update;
                                     older ev-proto versions don't have it, and the Errors tab says so)
  logs/conversations.db  conversations + messages - per-user chat history

Phase 0 has no "answer type" or "confident" field (ev-proto computes them but doesn't save them yet), so the
outcome below is worked out from the saved answer: ev-proto's fixed fallback sentences, and whether sources
were saved. Phase 1 replaces this guess with the real values.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import metrics as m
from .settings import REPHRASE_WINDOW_SECONDS, SLOW_ANSWER_SECONDS, USD_TO_AED

# ev-proto app/i18n.py fixed replies (en + ar). Keep in sync if those sentences change.
_CANNED = {
    "no_answer": [
        "I'm not able to pull that up right now.",
        "I'm not able to support that request at the moment.",
        "لا يمكنني الوصول إلى هذه المعلومة حاليًا.",
        "لا يمكنني دعم هذا الطلب في الوقت الحالي.",
    ],
    "failed": [
        "I couldn't finish that one",
        "لم أتمكن من إكمال هذا الطلب",
    ],
    "off_topic": [
        "Sorry, that's outside what I can help with.",
        "عذرًا، هذا خارج نطاق مساعدتي.",
    ],
}

OUTCOMES = {
    "answered": "Answered with sources",
    "no_answer": "Couldn't answer",
    "failed": "Failed to finish",
    "off_topic": "Off topic",
    "other": "Chat or no sources saved",
}

# error_log.kind -> what the dashboard calls it, and what it usually means
ERROR_KINDS = {
    "tool_error": ("Data system said no", "Zoho or MenaMe rejected the request: wrong field or filter, token or connection trouble"),
    "tool_crash": ("Tool crashed", "The code that talks to Zoho or MenaMe raised an error"),
    "claude_api": ("Claude API error", "Rate limit, overloaded, timeout or a bad key. The person got an error"),
    "cut_off": ("Answer cut off", "The answer hit the token limit; the person got 'couldn't finish'"),
    "round_limit": ("Too many steps", "No usable answer after the research limit; the person got 'couldn't finish'"),
    "server": ("App crashed", "Something else in the app failed"),
}


def _error_label(kind: str) -> str:
    return ERROR_KINDS.get(kind, (kind or "Other",))[0]


# qa_log columns added by ev-proto after Phase 0 (all optional)
_QA_EXTRA = ("answer_type", "confident", "language", "conversation_id", "turn_index", "tokens_in", "tokens_out",
             "cache_read", "cache_write", "model_calls", "model_requested", "rounds", "app_version", "finish",
             "grounding_ok", "figure_removed", "forced_resubmit")


def _public_details(d: dict) -> dict:
    # numbers and labels only: safe for viewers too
    return {k: v for k, v in d.items() if k != "language" and v is not None}


_ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿ]")
_DOUBT = re.compile(r"are you sure|double[- ]?check|check again|is that (all|right|correct)|really\?|"
                    r"متأكد|تأكد|تحقق|صحيح؟", re.I)
_WORD = re.compile(r"\w{3,}", re.UNICODE)


def classify(answer: str, sources: list) -> str:
    text = (answer or "").strip()
    for outcome, starts in _CANNED.items():
        if any(text.startswith(s) for s in starts):
            return outcome
    return "answered" if sources else "other"


def _similar(a: str, b: str) -> bool:
    wa, wb = set(_WORD.findall((a or "").lower())), set(_WORD.findall((b or "").lower()))
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.5


def _connect(path: str) -> sqlite3.Connection:
    # mode=ro: the dashboard can never change the agent's data
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


@dataclass
class AlertSettings:
    error_rate: float = 0.10
    unanswered_rate: float = 0.25
    wait_seconds: float = 10.0
    quiet_hours: float = 24.0
    failed_logins: int = 10


class WhatsAppSource:
    def __init__(self, logs_dir: str, tz, alert_settings: Optional[AlertSettings] = None) -> None:
        self.logs_dir = logs_dir
        self.tz = tz
        self.alert_settings = alert_settings or AlertSettings()
        self.audit_path = os.path.join(logs_dir, "audit.db")
        self.conv_path = os.path.join(logs_dir, "conversations.db")

    # ---------- raw reads ----------
    def _rows(self, path: str, table: str, sql: str, params=()) -> list:
        if not os.path.isfile(path):
            return []
        with closing(_connect(path)) as conn:
            if not _table_exists(conn, table):
                return []
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def _qa_extra_columns(self) -> list:
        # columns ev-proto added in Phase 1/2; older databases don't have them
        if not os.path.isfile(self.audit_path):
            return []
        with closing(_connect(self.audit_path)) as conn:
            have = {r[1] for r in conn.execute("PRAGMA table_info(qa_log)")}
        return [c for c in _QA_EXTRA if c in have]

    def questions(self, days: Optional[int] = None, now: Optional[datetime] = None) -> list:
        start = m.since(days, now)
        # read a little earlier than the window so "asked again" can see the question before the first one
        params: tuple = ()
        where = ""
        if start:
            where = "WHERE ts >= ?"
            params = ((start - _pad()).astimezone(timezone.utc).isoformat(),)
        extra = self._qa_extra_columns()
        extra_sql = "".join(f", {c}" for c in extra)
        raw = self._rows(self.audit_path, "qa_log",
                         "SELECT id, ts, user_id, name, role, question, answer, sources, model, cost_usd, "
                         f"elapsed_ms, ip{extra_sql} FROM qa_log {where} ORDER BY id", params)
        rows = []
        for r in raw:
            ts = m.parse_ts(r["ts"])
            if ts is None:
                continue
            try:
                sources = json.loads(r["sources"] or "[]")
            except (TypeError, ValueError):
                sources = []
            cost = float(r["cost_usd"] or 0)
            seconds = (r["elapsed_ms"] or 0) / 1000
            rows.append({
                "id": r["id"], "ts": ts, "user_id": r["user_id"] or "", "name": r["name"] or r["user_id"] or "?",
                "role": r["role"] or "", "question": r["question"] or "", "answer": r["answer"] or "",
                "sources": sources if isinstance(sources, list) else [], "model": r["model"] or "unknown",
                "cost_usd": cost, "cost_aed": cost * USD_TO_AED, "seconds": seconds, "ip": r["ip"] or "",
                "outcome": classify(r["answer"], sources if isinstance(sources, list) else []),
                "language": r.get("language") or ("ar" if _ARABIC.search(r["question"] or "") else "en"),
                "details": {c: r.get(c) for c in extra},
                "slow": seconds > SLOW_ANSWER_SECONDS, "asked_again": False, "doubted": False,
            })
        _mark_followups(rows)
        if start:
            rows = [r for r in rows if r["ts"] >= start]
        return rows

    def logins(self, days: Optional[int] = None, now: Optional[datetime] = None) -> list:
        start = m.since(days, now)
        raw = self._rows(self.audit_path, "auth_log",
                         "SELECT id, ts, identity, success, ip, reason FROM auth_log ORDER BY id")
        out = []
        for r in raw:
            ts = m.parse_ts(r["ts"])
            if ts is None or (start and ts < start):
                continue
            out.append({"id": r["id"], "ts": ts, "identity": r["identity"] or "?", "success": bool(r["success"]),
                        "ip": r["ip"] or "", "reason": r["reason"] or ""})
        return out

    def errors(self, days: Optional[int] = None, now: Optional[datetime] = None) -> list:
        start = m.since(days, now)
        params: tuple = ()
        where = ""
        if start:
            where = "WHERE ts >= ?"
            params = (start.astimezone(timezone.utc).isoformat(),)
        raw = self._rows(self.audit_path, "error_log",
                         "SELECT id, ts, kind, source, message, detail, user_id, name, question, path, ip "
                         f"FROM error_log {where} ORDER BY id", params)
        out = []
        for r in raw:
            ts = m.parse_ts(r["ts"])
            if ts is None or (start and ts < start):
                continue
            system = ""
            if r["kind"] == "tool_error":
                try:
                    system = (json.loads(r["detail"] or "{}") or {}).get("system") or ""
                except (TypeError, ValueError, AttributeError):
                    system = ""
            out.append({"id": r["id"], "ts": ts, "kind": r["kind"] or "other", "source": r["source"] or "",
                        "message": r["message"] or "", "detail": r["detail"] or "", "user_id": r["user_id"] or "",
                        "name": r["name"] or r["user_id"] or "", "question": r["question"] or "",
                        "path": r["path"] or "", "ip": r["ip"] or "", "system": system})
        return out

    def error_log_exists(self) -> bool:
        if not os.path.isfile(self.audit_path):
            return False
        with closing(_connect(self.audit_path)) as conn:
            return _table_exists(conn, "error_log")

    def error_summary(self, days: Optional[int], now: Optional[datetime] = None) -> dict:
        rows = self.errors(days, now)
        kinds = Counter(r["kind"] for r in rows)
        asked = len(self.questions(days, now)) if rows else 0
        hit = {(r["user_id"], r["question"]) for r in rows if r["question"]}
        return {
            "available": self.error_log_exists(),
            "totals": {
                "errors": len(rows),
                "people": len({r["user_id"] for r in rows if r["user_id"]}),
                "questions": len(hit),
                "question_share": round(len(hit) / asked, 3) if asked else None,
                "last": m.iso(max((r["ts"] for r in rows), default=None)),
            },
            "per_day": m.day_series(rows, self.tz, days, now=now),
            "by_kind": [{"key": k, "label": _error_label(k), "hint": ERROR_KINDS.get(k, ("", ""))[1], "count": v}
                        for k, v in kinds.most_common()],
            "by_source": m.top(Counter(r["source"] or "unknown" for r in rows), 10),
            "by_system": m.top(Counter(r["system"] for r in rows if r["system"]), 5),
            "kinds": {k: v[0] for k, v in ERROR_KINDS.items()},
        }

    def error_list(self, days: Optional[int], *, show_text: bool, kind: str = "", limit: int = 25, offset: int = 0,
                   now: Optional[datetime] = None) -> dict:
        rows = self.errors(days, now)
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        rows.sort(key=lambda r: r["ts"], reverse=True)
        return {"total": len(rows), "items": [public_error(r, show_text, full=False) for r in rows[offset:offset + limit]]}

    def error(self, eid: int, *, show_text: bool) -> Optional[dict]:
        for r in self.errors(None):
            if r["id"] == eid:
                return public_error(r, show_text, full=True)
        return None

    def alerts(self, now: Optional[datetime] = None) -> dict:
        """Checks over the last 24 hours. Counts only, never names or question text, so viewers can see them too."""
        now = now or datetime.now(timezone.utc)
        s = self.alert_settings
        out: list = []

        def add(key, level, title, detail, action, since=None):
            out.append({"key": key, "level": level, "title": title, "detail": detail, "action": action,
                        "since": m.iso(since) if isinstance(since, datetime) else since})

        if not os.path.isfile(self.audit_path):
            return {"alerts": [], "crit": 0, "warn": 0, "found": False}
        day = self.questions(1, now)
        n = len(day)

        if self.error_log_exists():
            errs = self.errors(1, now)
            hit = {(r["user_id"], r["question"]) for r in errs if r["question"]}
            if n >= 5 and len(hit) / n > s.error_rate:
                add("wa_error_rate", "crit", "Many questions hit an error",
                    f"{len(hit)} of {n} questions in the last 24 hours hit an error ({len(hit) / n:.0%}).",
                    "Open WhatsApp agent → Errors to see what went wrong and where.")
            api = [r for r in errs if r["kind"] == "claude_api"]
            if len(api) >= 3:
                add("wa_claude_api", "crit", "Claude API errors",
                    f"{len(api)} Claude API errors in the last 24 hours (rate limit, overloaded, timeout or key).",
                    "Check the Anthropic status page and the API key and usage limits in the Anthropic console.",
                    api[-1]["ts"])
            systems = Counter(r["system"] for r in errs if r["system"])
            if systems:
                system, count = systems.most_common(1)[0]
                if count >= 5:
                    add("wa_system", "warn", f"{system} keeps refusing requests",
                        f"{count} requests to {system} failed in the last 24 hours.",
                        f"Open Errors and filter by 'Data system said no'. Check the {system} token or connection.")

        unanswered = [r for r in day if r["outcome"] in ("no_answer", "failed")]
        if n >= 10 and len(unanswered) / n > s.unanswered_rate:
            add("wa_unanswered", "warn", "Many questions not answered",
                f"{len(unanswered)} of {n} questions in the last 24 hours got 'couldn't answer' or 'couldn't finish' "
                f"({len(unanswered) / n:.0%}).",
                "Open Questions and filter by Couldn't answer / Failed to finish to see which ones.")

        typical = m.percentile([r["seconds"] for r in day], 0.5)
        if n >= 10 and typical and typical > s.wait_seconds:
            add("wa_slow", "warn", "Answers are slow",
                f"Typical answer time in the last 24 hours is {typical:.1f} s (target {s.wait_seconds:.0f} s).",
                "Open People & models to see which model is slow; check Zoho and MenaMe response times.")

        last = self._rows(self.audit_path, "qa_log", "SELECT ts FROM qa_log ORDER BY ts DESC LIMIT 1")
        last_ts = m.parse_ts(last[0]["ts"]) if last else None
        workday = now.astimezone(self.tz).weekday() < 5  # Saturday and Sunday are the UAE weekend
        if s.quiet_hours and last_ts and workday and now - last_ts > timedelta(hours=s.quiet_hours):
            add("wa_quiet", "warn", "No questions",
                f"The last question was {(now - last_ts).total_seconds() / 3600:.0f} hours ago.",
                "Check that the WhatsApp agent is running and that people can log in.", last_ts)

        fails = [r for r in self.logins(1, now) if not r["success"]]
        worst = max([*Counter(r["identity"] for r in fails).values(), *Counter(r["ip"] for r in fails).values(), 0])
        if worst >= s.failed_logins:
            add("wa_logins", "crit", "Possible password guessing",
                f"{len(fails)} failed logins in the last 24 hours, {worst} from the same person or address.",
                "Open Security → Logins (admins) to see who and from where; block the address if it isn't staff.")

        today = sum(r["cost_aed"] for r in day)
        before = self.questions(15, now)
        past = sum(r["cost_aed"] for r in before if r["ts"] < now - timedelta(days=1)) / 14
        if today > 10 and past and today > 3 * past:
            add("wa_cost", "warn", "Cost jump",
                f"The last 24 hours cost AED {today:,.2f}, more than 3 times the daily average (AED {past:,.2f}).",
                "Open People & models to see who asked the most and which model answered.")

        crit = sum(a["level"] == "crit" for a in out)
        return {"alerts": out, "crit": crit, "warn": len(out) - crit, "found": True}

    def conversation_stats(self, days: Optional[int] = None, now: Optional[datetime] = None) -> dict:
        start = m.since(days, now)
        convs = self._rows(self.conv_path, "conversations",
                           "SELECT c.id, c.user_id, c.created_at, "
                           "(SELECT COUNT(*) FROM messages x WHERE x.conversation_id=c.id AND x.role='user') AS asks "
                           "FROM conversations c")
        if start:
            convs = [c for c in convs if (m.parse_ts(c["created_at"]) or start) >= start]
        asks = [c["asks"] for c in convs]
        return {
            "available": os.path.isfile(self.conv_path),
            "conversations": len(convs),
            "avg_questions": m.rnd(sum(asks) / len(asks), 1) if asks else None,
            "multi_question_share": m.rnd(sum(1 for a in asks if a > 1) / len(asks), 3) if asks else None,
            "people": len({c["user_id"] for c in convs}),
        }

    # ---------- dashboard views ----------
    def status(self) -> dict:
        last = self._rows(self.audit_path, "qa_log", "SELECT ts FROM qa_log ORDER BY id DESC LIMIT 1")
        return {
            "logs_dir": self.logs_dir,
            "audit_db": os.path.isfile(self.audit_path),
            "conversations_db": os.path.isfile(self.conv_path),
            "last_question": m.iso(m.parse_ts(last[0]["ts"])) if last else None,
        }

    def summary(self, days: Optional[int], now: Optional[datetime] = None) -> dict:
        rows = self.questions(days, now)
        n = len(rows)
        cost_aed = sum(r["cost_aed"] for r in rows)
        secs = [r["seconds"] for r in rows]
        outcomes = Counter(r["outcome"] for r in rows)

        by_model: dict = defaultdict(list)
        by_user: dict = defaultdict(list)
        by_role: Counter = Counter()
        modules: Counter = Counter()
        for r in rows:
            by_model[r["model"]].append(r)
            by_user[r["user_id"]].append(r)
            by_role[r["role"] or "unknown"] += 1
            for s in r["sources"]:
                if isinstance(s, dict) and s.get("module"):
                    modules[str(s["module"])] += 1

        return {
            "days": days,
            "totals": {
                "questions": n,
                "people": len(by_user),
                "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
                "cost_aed": round(cost_aed, 2),
                "cost_aed_per_question": round(cost_aed / n, 3) if n else None,
                "seconds_p50": m.rnd(m.percentile(secs, 0.5), 1),
                "seconds_p95": m.rnd(m.percentile(secs, 0.95), 1),
                "seconds_avg": m.rnd(sum(secs) / n, 1) if n else None,
                "slow": sum(r["slow"] for r in rows),
                "asked_again": sum(r["asked_again"] for r in rows),
                "doubted": sum(r["doubted"] for r in rows),
                "arabic_share": round(sum(r["language"] == "ar" for r in rows) / n, 3) if n else None,
            },
            "outcomes": [{"key": k, "label": OUTCOMES[k], "count": outcomes.get(k, 0)} for k in OUTCOMES],
            "per_day": m.day_series(rows, self.tz, days, now=now),
            "cost_per_day": m.day_series(rows, self.tz, days, value=lambda r: r["cost_aed"], now=now),
            "heatmap": m.heatmap(rows, self.tz),
            "by_model": sorted([{
                "model": k, "questions": len(v), "cost_aed": round(sum(r["cost_aed"] for r in v), 2),
                "seconds_p50": m.rnd(m.percentile([r["seconds"] for r in v], 0.5), 1),
            } for k, v in by_model.items()], key=lambda x: -x["questions"]),
            "by_user": sorted([{
                "name": v[-1]["name"], "role": v[-1]["role"], "questions": len(v),
                "cost_aed": round(sum(r["cost_aed"] for r in v), 2),
                "answered_share": round(sum(r["outcome"] == "answered" for r in v) / len(v), 3),
            } for v in by_user.values()], key=lambda x: -x["questions"])[:10],
            "by_role": m.top(by_role),
            "top_modules": m.top(modules),
            "conversations": self.conversation_stats(days, now),
            "status": self.status(),
        }

    def question_list(self, days: Optional[int], *, show_text: bool, outcome: str = "", flag: str = "",
                      q: str = "", limit: int = 50, offset: int = 0, now: Optional[datetime] = None) -> dict:
        rows = self.questions(days, now)
        if outcome:
            rows = [r for r in rows if r["outcome"] == outcome]
        if flag in ("slow", "asked_again", "doubted"):
            rows = [r for r in rows if r[flag]]
        if q and show_text:
            ql = q.lower()
            rows = [r for r in rows if ql in r["question"].lower() or ql in r["answer"].lower()
                    or ql in r["name"].lower()]
        rows.sort(key=lambda r: r["ts"], reverse=True)
        page = rows[offset:offset + limit]
        return {"total": len(rows), "items": [public_question(r, show_text, full=False) for r in page]}

    def steps(self, qid: int) -> list:
        """Model calls and tool calls behind one answer (ev-proto Phase 2). Callers must only pass this to admins:
        it holds the model's written plan and the queries sent to Zoho / MenaMe."""
        raw = self._rows(self.audit_path, "steps", "SELECT data FROM steps WHERE qa_id=? ORDER BY id", (qid,))
        out = []
        for r in raw:
            try:
                out.append(json.loads(r["data"] or "{}"))
            except (TypeError, ValueError):
                continue
        return out

    def question(self, qid: int, *, show_text: bool) -> Optional[dict]:
        for r in self.questions(None):
            if r["id"] == qid:
                return public_question(r, show_text, full=True)
        return None

    def login_summary(self, days: Optional[int], now: Optional[datetime] = None) -> dict:
        rows = self.logins(days, now)
        fails = [r for r in rows if not r["success"]]
        return {
            "total": len(rows), "failed": len(fails), "succeeded": len(rows) - len(fails),
            "people": len({r["identity"] for r in rows if r["success"]}),
            "fails_per_day": m.day_series(fails, self.tz, days, now=now),
            "ok_per_day": m.day_series([r for r in rows if r["success"]], self.tz, days, now=now),
            "top_failed_identities": m.top(Counter(r["identity"] for r in fails), 8),
            "top_failed_ips": m.top(Counter(r["ip"] or "unknown" for r in fails), 8),
            "recent": [{"ts": m.iso(r["ts"]), "identity": r["identity"], "success": r["success"], "ip": r["ip"],
                        "reason": r["reason"]} for r in sorted(rows, key=lambda r: r["ts"], reverse=True)[:50]],
        }


def _pad() -> timedelta:
    return timedelta(seconds=REPHRASE_WINDOW_SECONDS + 60)


def _mark_followups(rows: list) -> None:
    """Flag answers the person didn't seem happy with: they asked again in other words, or asked 'are you sure?'."""
    by_user: dict = defaultdict(list)
    for r in rows:
        by_user[r["user_id"]].append(r)
    for items in by_user.values():
        items.sort(key=lambda r: r["ts"])
        for prev, nxt in zip(items, items[1:]):
            gap = (nxt["ts"] - prev["ts"]).total_seconds()
            if gap > REPHRASE_WINDOW_SECONDS:
                continue
            if _DOUBT.search(nxt["question"]):
                prev["doubted"] = True
            elif _similar(prev["question"], nxt["question"]):
                prev["asked_again"] = True


def public_error(r: dict, show_text: bool, full: bool) -> dict:
    """Type, place and time for everyone. The message, question, tool input, traceback and IP only for admins:
    they can hold the person's search words or record values."""
    out = {
        "id": r["id"], "ts": m.iso(r["ts"]), "kind": r["kind"], "kind_label": _error_label(r["kind"]),
        "source": r["source"], "system": r["system"], "name": r["name"],
        "message": r["message"] if show_text else None,
        "question": r["question"] if show_text else None,
    }
    if full:
        out["kind_hint"] = ERROR_KINDS.get(r["kind"], ("", ""))[1]
        out["detail"] = r["detail"] if show_text else None
        out["path"] = r["path"]
        out["ip"] = r["ip"] if show_text else None
    return out


def public_question(r: dict, show_text: bool, full: bool) -> dict:
    """What the browser gets. Question/answer text, sources and IPs only for people allowed to see content."""
    out = {
        "id": r["id"], "ts": m.iso(r["ts"]), "name": r["name"], "role": r["role"], "model": r["model"],
        "cost_aed": round(r["cost_aed"], 4), "cost_usd": round(r["cost_usd"], 5), "seconds": round(r["seconds"], 2),
        "outcome": r["outcome"], "outcome_label": OUTCOMES[r["outcome"]], "language": r["language"],
        "flags": [f for f in ("slow", "asked_again", "doubted") if r[f]],
        "source_count": len(r["sources"]),
        "question": r["question"] if show_text else None,
    }
    if full:
        out["answer"] = r["answer"] if show_text else None
        out["sources"] = r["sources"] if show_text else None
        out["ip"] = r["ip"] if show_text else None
        out["details"] = _public_details(r.get("details") or {})
    return out
