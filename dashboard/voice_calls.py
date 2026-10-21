"""Reads what the real voice agent (ev-voice-agent, LiveKit) saves. READ-ONLY.

  <data>/calls/<call>.json         one call: transcript, CRM actions, tool calls, per-turn timings and tokens,
                                   phone line result, recording, transfer, classification, errors,
                                   post-call LLM check, versions, settings, which LLM server answered
  <data>/calls/<call>.raw.json     a call whose post-call work hasn't finished yet (or the agent crashed
                                   before it did): counted as "being processed", never as a call
  <data>/calls/<call>/llm.jsonl          every LLM request (stage "call" or "post_call")
  <data>/calls/<call>/crm_changes.jsonl  every CRM write, before -> after
  <data>/speech_evals/<run>.json   speech-to-text / text-to-speech accuracy runs (python -m evals.run_speech)

Older call records (before the agent recorded these fields) still load; missing values show as unknown.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import metrics as m

CLASSIFICATION = {
    "qualified": "Qualified", "interested": "Interested, details missing", "callback": "Callback booked",
    "not_interested": "Not interested", "do_not_call": "Do not call", "wrong_number": "Wrong number",
    "no_conversation": "No conversation", "not_reached": "Not reached",
}
LINE = {
    "answered": "Answered", "busy": "Busy", "no_answer": "No answer", "declined": "Declined",
    "invalid_number": "Invalid number", "not_allowed": "Trunk refused the call", "line_error": "Line or 3CX error",
    "failed": "Failed", "browser": "Browser or console test",
}
ENDED_BY = {"agent": "Agent closed the call", "lead": "Lead hung up", "transferred": "Transferred to a person",
            "not_reached": "Lead not reached", "error": "Stopped: error", "shutdown": "Worker shut down"}
STAGES = [("end_to_end", "Caller stops → agent's first audio"), ("eou", "Speech-to-text (end of speech)"),
          ("llm_ttft", "LLM first token"), ("tts_ttfb", "Text-to-speech first audio")]
STAGE_LABELS = {"stt": "Speech-to-text", "llm": "LLM", "tts": "Text-to-speech", "vad": "Voice detection",
                "crm": "CRM", "session": "Call session", "post_call": "Post-call check"}
SLOW_TURN_SECONDS = 1.5
RAW_SUFFIX = ".raw.json"
MAX_LOG_LINES = 500          # per log file shown in one call's detail
_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


def _read_jsonl(path: str, limit: int = MAX_LOG_LINES) -> list:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if len(out) >= limit:
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        pass
    return out


def _errors(raw: dict) -> list:
    """The record's `errors` list (every pipeline stage), plus the older single `error` field."""
    out = [dict(e) for e in (raw.get("errors") or []) if isinstance(e, dict)]  # copies: raw is cached
    if raw.get("error") and not any(e.get("stage") == "post_call" for e in out):
        out.append({"stage": "post_call", "error": raw["error"]})
    for e in out:
        e["stage"] = e.get("stage") or "session"
        e["stage_label"] = STAGE_LABELS.get(e["stage"], e["stage"])
    return out


def _load(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def parse_call(raw: dict, call_id: str) -> dict:
    metrics = raw.get("metrics") or {}
    turns = metrics.get("turn_details") or []
    started = m.parse_ts(raw.get("started_at"))
    ended = m.parse_ts(raw.get("ended_at"))
    line = raw.get("line")
    line_key = (line or {}).get("result") or "browser"
    extraction = raw.get("extraction") or {}
    cls = raw.get("classification") or extraction.get("outcome") or ("not_reached" if line_key != "answered"
                                                                     and line else None)
    tokens = metrics.get("tokens") or {}
    ttfts = [t["llm_ttft"] for t in turns if t.get("llm_ttft") is not None]
    if not ttfts and (metrics.get("llm_ttft") or {}).get("p50"):
        ttfts = [metrics["llm_ttft"]["p50"]]  # older records: only the summary was saved
    e2e = [t["end_to_end"] for t in turns if t.get("end_to_end") is not None]
    transfer = raw.get("transfer")
    recording = raw.get("recording")
    models = raw.get("models") or {}
    settings = raw.get("settings") or {}
    errors = _errors(raw)
    post_llm = raw.get("post_call_llm") or {}
    llm_requests = metrics.get("llm_requests") if isinstance(metrics.get("llm_requests"), dict) else {}
    actions = raw.get("actions") or []
    fallback = raw.get("fallback") or None
    # a callback with a time: booked by the agent during the call, or by the LLM-failure fallback
    callback_at = None
    for a in actions:
        if isinstance(a, dict) and a.get("action") == "schedule_callback" and a.get("when"):
            callback_at = m.parse_ts(a["when"]) or callback_at
    if fallback and fallback.get("callback_at"):
        callback_at = m.parse_ts(fallback["callback_at"]) or callback_at
    # hot / warm / cold: what the agent saved, else what the post-call check filled in or heard
    interest = None
    for a in actions:
        if isinstance(a, dict) and a.get("interest_level"):
            interest = a["interest_level"]
    interest = interest or (raw.get("filled_after_call") or {}).get("interest_level") or extraction.get("interest_level")
    return {
        "id": call_id, "lead_id": raw.get("lead_id", "?"), "room": raw.get("room", ""),
        "ts": started, "ended_at": ended,
        "duration_seconds": round((ended - started).total_seconds(), 1) if started and ended else None,
        "line": line, "line_result": line_key, "line_label": LINE.get(line_key, line_key),
        "classification": cls, "classification_label": CLASSIFICATION.get(cls, cls or "Unknown"),
        "ended_by": raw.get("ended_by") or "unknown",
        "ended_by_label": ENDED_BY.get(raw.get("ended_by") or "", raw.get("ended_by") or "Unknown"),
        "agent_closed": bool(raw.get("agent_closed", raw.get("ended_by") == "agent")),
        "transfer": transfer, "transferred": bool(transfer and transfer.get("ok")),
        "transfer_asked": bool(transfer),
        "recording": recording, "recorded": bool(recording and recording.get("ok")),
        "models": models,
        # vllm (production) or ollama (testing); older records don't say
        "served_by": models.get("served_by") or (models.get("llm_server") or {}).get("served_by")
        or settings.get("llm_backend") or None,
        "versions": raw.get("versions") or {}, "settings": settings,
        "errors": errors, "error_count": len(errors), "error_stages": sorted({e["stage"] for e in errors}),
        "post_call_seconds": raw.get("post_call_seconds"),
        "post_call_llm": post_llm or None,
        "interruptions": metrics.get("interruptions"),
        "llm_errors": llm_requests.get("errors"), "llm_cancelled": llm_requests.get("cancelled"),
        "user_speech_seconds": metrics.get("user_speech_seconds"),
        "log_files": raw.get("log_files") or {},
        "turns": turns, "voice_turns": metrics.get("turns", 0), "llm_turns": metrics.get("llm_turns", len(ttfts)),
        "ttft_min": min(ttfts) if ttfts else None, "ttft_median": m.rnd(m.percentile(ttfts, 0.5), 3),
        "ttft_max": max(ttfts) if ttfts else None,
        "e2e_min": min(e2e) if e2e else None, "e2e_median": m.rnd(m.percentile(e2e, 0.5), 3),
        "e2e_max": max(e2e) if e2e else None,
        "tokens_in": tokens.get("prompt"), "tokens_out": tokens.get("completion"), "tokens_total": tokens.get("total"),
        "tokens_per_second": tokens.get("tokens_per_second"),
        "repeat_requests": raw.get("repeat_requests"),
        "tool_calls": raw.get("tool_calls") or [], "actions": raw.get("actions") or [],
        "transcript": raw.get("transcript") or [], "summary": extraction.get("summary"),
        "filled_after_call": raw.get("filled_after_call") or {}, "disagreements": raw.get("disagreements") or [],
        "error": raw.get("error"), "metrics": metrics,
        # from events.jsonl (call_started), filled in by VoiceCallSource.call()
        "lead_name": None, "lead_status_before": None,
        "fallback": fallback,
        "callback_at": callback_at,
        # the post-call check heard a callback request; the agreed time is in the caller's words (admins only)
        "callback_requested": bool(callback_at or extraction.get("callback_requested") or cls == "callback"),
        "callback_text": extraction.get("callback_time_text"),
        "interest_level": interest,
        "do_not_call": cls == "do_not_call" or any(isinstance(a, dict) and a.get("action") == "mark_do_not_call"
                                                   for a in actions),
    }


TEXT_FIELDS = ("transcript", "tool_calls", "actions", "summary", "filled_after_call", "disagreements", "lead_name",
               "callback_text")


def llm_rows(lines: list) -> list:
    """llm.jsonl -> one row per LLM request: timings, tokens, reply and tool calls."""
    rows = []
    for x in lines:
        stage = x.get("stage") or "call"
        mt = x.get("metrics") or {}
        row = {
            "stage": stage, "n": x.get("n") or x.get("attempt"), "ts": x.get("ts") or x.get("started_at"),
            "ttft": x.get("ttft", mt.get("ttft")), "seconds": x.get("seconds", mt.get("duration")),
            "prompt_tokens": mt.get("prompt_tokens", x.get("prompt_tokens")),
            "cached_tokens": mt.get("prompt_cached_tokens"),
            "completion_tokens": mt.get("completion_tokens", x.get("completion_tokens")),
            "tokens_per_second": mt.get("tokens_per_second"), "reasoning_tokens": mt.get("reasoning_tokens"),
            "tools_called": [t.get("name") for t in x.get("tool_calls") or [] if isinstance(t, dict)],
            "cancelled": bool(x.get("cancelled")),
            "ok": bool(x.get("ok", not x.get("error"))) and not x.get("cancelled"),
            "failed": bool(x.get("error")),
        }
        row["reply"] = x.get("text") if stage == "call" else x.get("reply")
        row["tool_calls"] = x.get("tool_calls") or []
        row["error"] = x.get("error")
        rows.append(row)
    return rows


def crm_rows(lines: list) -> list:
    """crm_changes.jsonl -> one row per CRM write: which fields changed and their values."""
    rows = []
    for x in lines:
        changed = x.get("changed") or {}
        row = {"ts": x.get("ts"), "source": x.get("source") or "call", "action": x.get("action"),
               "ok": bool(x.get("ok", True)), "seconds": x.get("seconds"), "fields": sorted(changed),
               "changed": changed, "error": x.get("error")}
        rows.append(row)
    return rows


def public_call(c: dict, *, full: bool) -> dict:
    hidden = {"ts", "ended_at", "metrics", "turns", "errors", "llm_log", "crm_log", *TEXT_FIELDS}
    out = {k: v for k, v in c.items() if k not in hidden}
    out["ts"], out["ended_at"] = m.iso(c["ts"]), m.iso(c["ended_at"])
    out["callback_at"] = m.iso(c.get("callback_at"))
    out["tool_call_count"] = c.get("tool_call_count", len(c["tool_calls"] or []))
    if full:
        out["turns"] = c["turns"]
        for k in TEXT_FIELDS:
            out[k] = c[k]
        out["errors"] = c["errors"]
        out["llm_requests"] = llm_rows(c.get("llm_log") or [])
        out["crm_changes"] = crm_rows(c.get("crm_log") or [])
    return out


def slim(c: dict) -> dict:
    """What the lists, summaries and charts need from one call: no transcript or raw metrics.
    The call's detail view re-reads the file (VoiceCallSource.call), so nothing is lost."""
    out = dict(c)
    out["tool_call_count"] = len(c["tool_calls"])
    out["transcript"], out["tool_calls"], out["summary"], out["metrics"] = [], [], None, {}
    return out


class VoiceCallSource:
    def __init__(self, data_dir: str, tz, cache_seconds: float = 15.0, costs: Optional[dict] = None) -> None:
        self.data_dir = data_dir
        # AED: {"gpu_month", "other_month", "line_minute"}; all 0 = not set (see cost())
        self.costs = {"gpu_month": 0.0, "other_month": 0.0, "line_minute": 0.0, **(costs or {})}
        self.calls_dir = os.path.join(data_dir, "calls")
        self.evals_dir = os.path.join(data_dir, "speech_evals")
        self.tz = tz
        self.cache_seconds = cache_seconds
        self._cache: dict = {}    # raw JSON per file, for the speech runs
        self._parsed: dict = {}   # path -> ((mtime, size), slim parsed call or None)
        self._listed: Optional[tuple] = None  # (monotonic expiry, [calls, newest first])
        self._memo: dict = {}     # (what, args) -> (the call list it was worked out from, result)
        self._refreshing: set = set()
        self._lock = threading.Lock()
        self._names: dict = {}    # call id -> (lead name, status before the call) from events.jsonl

    def _read_dir(self, folder: str) -> list:
        out = []
        if not os.path.isdir(folder):
            return out
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json") or name.endswith(RAW_SUFFIX):
                continue
            path = os.path.join(folder, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = (st.st_mtime_ns, st.st_size)
            hit = self._cache.get(path)
            if not hit or hit[0] != key:
                hit = (key, _load(path))
                self._cache[path] = hit
            if hit[1] is not None:
                out.append((name[:-5], hit[1]))
        return out

    def _all_calls(self) -> list:
        """Every finished call, newest first. A file is parsed once and again only when it changes;
        the folder itself is listed at most every `cache_seconds` (one page asks several times).
        When nothing changed, the same list object comes back, so summaries worked out from it are reused."""
        with self._lock:
            if self._listed and self._listed[0] > time.monotonic():
                return self._listed[1]
            seen: dict = {}
            changed = False
            try:
                entries = os.scandir(self.calls_dir) if os.path.isdir(self.calls_dir) else []
            except OSError:
                entries = []
            with entries if hasattr(entries, "close") else contextlib.nullcontext():
                for entry in entries:
                    name = entry.name
                    if not name.endswith(".json") or name.endswith(RAW_SUFFIX):
                        continue
                    try:
                        st = entry.stat()  # free on Windows: it comes with the folder listing
                    except OSError:
                        continue
                    key = (st.st_mtime_ns, st.st_size)
                    hit = self._parsed.get(entry.path)
                    if not hit or hit[0] != key:
                        raw = _load(entry.path)
                        hit = (key, slim(parse_call(raw, name[:-5])) if raw else None)
                        self._parsed[entry.path] = hit
                        changed = True
                    seen[entry.path] = hit
            if len(seen) != len(self._parsed):  # files that disappeared
                self._parsed = seen
                changed = True
            previous = self._listed[1] if self._listed else None
            if changed or previous is None:
                out = [hit[1] for hit in seen.values() if hit[1] is not None and hit[1]["ts"]]
                out.sort(key=lambda c: c["ts"], reverse=True)
            else:
                out = previous
            self._listed = (time.monotonic() + self.cache_seconds, out)
            return out

    # above this many calls, a summary that is out of date is shown at once and worked out again in the
    # background (2-4 s at 84,000 calls), so a page never waits for it; below it, it is worked out right away
    BACKGROUND_REFRESH_CALLS = 5000

    def _remember(self, what: str, args: tuple, now, work):
        """Reuse a summary while the call list is the same one (see _all_calls); `now` is for tests."""
        if now is not None:
            return work()
        listed = self._all_calls()
        key = (what, args)
        hit = self._memo.get(key)
        if hit and hit[0] is listed:
            return hit[1]
        if hit and len(listed) > self.BACKGROUND_REFRESH_CALLS:
            if key not in self._refreshing:
                self._refreshing.add(key)

                def refresh() -> None:
                    try:
                        self._memo[key] = (listed, work())
                    except Exception:  # keep the old answer; the next request tries again
                        pass
                    finally:
                        self._refreshing.discard(key)

                threading.Thread(target=refresh, daemon=True).start()
            return hit[1]
        value = work()
        if len(self._memo) > 64:
            self._memo.clear()
        self._memo[key] = (listed, value)
        return value

    def calls(self, days: Optional[int] = None, now=None) -> list:
        start = m.since(days, now)
        return [c for c in self._all_calls() if not start or c["ts"] >= start]

    def call(self, call_id: str) -> Optional[dict]:
        if not call_id or not _SAFE.match(call_id) or call_id.endswith(".raw") or call_id in (".", ".."):
            return None
        raw = _load(os.path.join(self.calls_dir, call_id + ".json"))
        if not raw:
            return None
        c = parse_call(raw, call_id)
        # the call's own log folder: data/calls/<call_id>/ (only when the record is there too)
        folder = os.path.join(self.calls_dir, call_id)
        c["llm_log"] = _read_jsonl(os.path.join(folder, "llm.jsonl"))
        c["crm_log"] = _read_jsonl(os.path.join(folder, "crm_changes.jsonl"))
        started = next((e for e in _read_jsonl(os.path.join(folder, "events.jsonl"))
                        if e.get("event") == "call_started"), {})
        c["lead_name"] = started.get("lead_name")
        c["lead_status_before"] = started.get("lead_status")
        return c

    def lead_start(self, call_id: str) -> tuple:
        """(lead name, lead status before the call) from the call's events.jsonl, cached."""
        if call_id not in self._names:
            folder = os.path.join(self.calls_dir, call_id)
            started = next((e for e in _read_jsonl(os.path.join(folder, "events.jsonl"), limit=20)
                            if e.get("event") == "call_started"), {})
            self._names[call_id] = (started.get("lead_name"), started.get("lead_status"))
        return self._names[call_id]

    # ---------- calls grouped by client ----------
    def clients(self, days: Optional[int], *, q: str = "", min_calls: int = 0, sort: str = "calls",
                offset: int = 0, limit: int = 50, now=None) -> dict:
        """One row per client (lead) with a call in the period. Attempts today / in 7 days count every
        phone call, whatever the period, so they match the agent's calling limits."""
        rows = self._remember("clients", (days,), now, lambda: self._client_rows(days, now))
        q = q.strip().lower()
        if q:
            rows = [r for r in rows if q in r["lead_id"].lower()
                    or q in (self.lead_start(r["last_call_id"])[0] or "").lower()]
        if min_calls:
            rows = [r for r in rows if r["calls"] >= min_calls]
        keys = {"calls": lambda r: (-r["calls"], r["last_ts"] and -r["last_ts"].timestamp()),
                "last": lambda r: -r["last_ts"].timestamp(),
                "today": lambda r: (-r["attempts_today"], -r["last_ts"].timestamp())}
        rows = sorted(rows, key=keys.get(sort, keys["calls"]))
        page = rows[offset:offset + limit]
        items = []
        for r in page:
            item = {k: (m.iso(v) if k in ("first_ts", "last_ts", "callback_at") else v) for k, v in r.items()}
            item["name"] = self.lead_start(r["last_call_id"])[0]
            items.append(item)
        return {"total": len(rows), "items": items, "offset": offset, "limit": limit}

    def _client_rows(self, days: Optional[int], now=None) -> list:
        now = now or datetime.now(timezone.utc)
        today = now.astimezone(self.tz).date()
        everything: dict = defaultdict(list)
        for c in self._all_calls():
            everything[c["lead_id"]].append(c)
        in_range = {c["lead_id"] for c in self.calls(days, now)}
        start = m.since(days, now)
        rows = []
        for lead_id in in_range:
            all_calls = everything[lead_id]                       # newest first
            cs = [c for c in all_calls if not start or c["ts"] >= start]
            last = cs[0]
            phone = [c for c in all_calls if c["line"]]
            numbers = [c["line"].get("number") for c in phone if c["line"].get("number")]
            callback = next((c["callback_at"] for c in all_calls if c["callback_at"]), None)
            rows.append({
                "lead_id": lead_id, "number": numbers[0] if numbers else None,
                "calls": len(cs), "reached": sum(1 for c in cs if c["line_result"] in ("answered", "browser")),
                "phone_calls": sum(1 for c in cs if c["line"]),
                "first_ts": cs[-1]["ts"], "last_ts": last["ts"], "last_call_id": last["id"],
                "last_label": last["classification_label"], "last_ended": last["ended_by_label"],
                "last_line": last["line_label"],
                "attempts_today": sum(1 for c in phone if c["ts"].astimezone(self.tz).date() == today),
                "attempts_7d": sum(1 for c in phone if now - timedelta(days=7) < c["ts"] <= now),
                "callback_at": callback,
                "callback_requested": any(c["callback_requested"] for c in cs),
                "do_not_call": any(c["do_not_call"] for c in all_calls),
                "transferred": any(c["transferred"] for c in cs),
                "errors": sum(c["error_count"] for c in cs),
            })
        return rows

    # ---------- sales follow-up ----------
    FOLLOW_UP = [
        ("callback", "Callback due", "The client agreed to be called back. Overdue first."),
        ("person", "Asked for a person", "The client asked to speak to someone and wasn't transferred."),
        ("hot", "Hot or qualified", "Qualified, or interest 'hot', in the last 7 days: hand to sales."),
        ("llm_failure", "Call stopped by a technical problem", "The AI failed mid-call (the agent booked a callback)."),
    ]

    def follow_up(self, *, now=None, per_group: int = 200) -> dict:
        """Who the sales team should contact, from each client's calls. Read-only: an item goes away
        when a newer call to that client happens (or, for hot leads, after 7 days). Do-not-call clients are left out."""
        now = now or datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        by_lead: dict = defaultdict(list)
        for c in self._all_calls():
            by_lead[c["lead_id"]].append(c)  # newest first
        groups: dict = {k: [] for k, _, _ in self.FOLLOW_UP}
        for lead_id, cs in by_lead.items():
            if any(c["do_not_call"] for c in cs):
                continue
            last = cs[0]
            base = {"lead_id": lead_id, "last_ts": last["ts"], "last_call_id": last["id"],
                    "last_label": last["classification_label"],
                    "number": next((c["line"].get("number") for c in cs if c["line"] and c["line"].get("number")), None)}
            if last["callback_requested"]:
                due = last["callback_at"]
                groups["callback"].append({**base, "call_id": last["id"], "due": due,
                                           "when": "none" if not due else "overdue" if due < now
                                           else "today" if due.astimezone(self.tz).date() == now.astimezone(self.tz).date()
                                           else "later",
                                           "callback_text": last["callback_text"]})
            if last["transfer_asked"] and not last["transferred"]:
                groups["person"].append({**base, "call_id": last["id"], "due": None})
            if last["ts"] >= week_ago and (last["classification"] == "qualified" or last["interest_level"] == "hot"):
                groups["hot"].append({**base, "call_id": last["id"], "due": None, "interest": last["interest_level"]})
            if last["ended_by"] == "llm_failure" and last["ts"] >= week_ago:
                groups["llm_failure"].append({**base, "call_id": last["id"], "due": last["callback_at"]})
        order = {"overdue": 0, "today": 1, "later": 2, "none": 3}
        groups["callback"].sort(key=lambda x: (order[x["when"]], x["due"] or x["last_ts"]))
        for k in ("person", "hot", "llm_failure"):
            groups[k].sort(key=lambda x: x["last_ts"], reverse=True)
        out = []
        for key, label, hint in self.FOLLOW_UP:
            items = []
            for x in groups[key][:per_group]:
                item = {k: (m.iso(v) if isinstance(v, datetime) else v) for k, v in x.items()}
                item["name"] = self.lead_start(x["call_id"])[0]
                full = self.call(x["call_id"])
                item["summary"] = full["summary"] if full else None
                items.append(item)
            out.append({"key": key, "label": label, "hint": hint, "count": len(groups[key]), "items": items})
        return {"groups": out, "total": sum(g["count"] for g in out),
                "overdue": sum(1 for x in groups["callback"] if x["when"] == "overdue")}

    def client_calls(self, lead_id: str) -> Optional[dict]:
        """Every call with this client, newest first (numbers only; open a call for the rest)."""
        if not lead_id or not _SAFE.match(lead_id):
            return None
        cs = [c for c in self._all_calls() if c["lead_id"] == lead_id]
        if not cs:
            return None
        name, _ = self.lead_start(cs[0]["id"])
        return {"lead_id": lead_id, "name": name, "calls": [public_call(c, full=False) for c in cs]}

    def processing(self) -> int:
        """Calls whose post-call work hasn't finished: a raw file with no final record next to it."""
        if not os.path.isdir(self.calls_dir):
            return 0
        names = set(os.listdir(self.calls_dir))
        return sum(1 for n in names if n.endswith(RAW_SUFFIX) and n[:-len(RAW_SUFFIX)] + ".json" not in names)

    def status(self) -> dict:
        calls = self.calls()
        return {"data_dir": self.data_dir, "found": os.path.isdir(self.calls_dir), "calls": len(calls),
                "processing": self.processing(),
                "last_call": m.iso(calls[0]["ts"]) if calls else None,
                "speech_runs": len(self._read_dir(self.evals_dir))}

    def summary(self, days: Optional[int], now=None) -> dict:
        return self._remember("summary", (days,), now, lambda: self._summary(days, now))

    def cost(self, cs: list, days: Optional[int], now=None) -> dict:
        """What the calls in the period cost, in AED: the period's share of the monthly costs (GPU server,
        anything else fixed) plus phone minutes on answered calls. Per call, per reached call, per qualified lead."""
        k = self.costs
        monthly = (k["gpu_month"] or 0) + (k["other_month"] or 0)
        rate = k["line_minute"] or 0
        if days:
            period = days
        elif cs:
            first = min(c["ts"] for c in cs).astimezone(self.tz).date()
            period = max(1, ((now or datetime.now(timezone.utc)).astimezone(self.tz).date() - first).days + 1)
        else:
            period = 30
        answered = [c for c in cs if c["line_result"] == "answered"]
        minutes = sum((c["duration_seconds"] or 0) for c in answered) / 60
        fixed, line = monthly * period / 30, minutes * rate
        total = fixed + line
        reached = sum(1 for c in cs if c["line_result"] in ("answered", "browser"))
        qualified = sum(1 for c in cs if c["classification"] == "qualified")
        line_per_day = m.day_series(answered, self.tz, period, now=now,
                                    value=lambda c: (c["duration_seconds"] or 0) / 60 * rate)
        per_day = [{**d, "value": round(d["value"] + monthly / 30, 2)} for d in line_per_day]
        div = lambda a, b: round(a / b, 2) if b else None  # noqa: E731
        return {
            "configured": bool(monthly or rate), "currency": "AED", "period_days": period,
            "monthly": round(monthly, 2), "line_rate": rate,
            "fixed": round(fixed, 2), "line": round(line, 2), "line_minutes": round(minutes, 1),
            "total": round(total, 2), "calls": len(cs), "reached": reached, "qualified": qualified,
            "per_call": div(total, len(cs)), "per_reached": div(total, reached), "per_qualified": div(total, qualified),
            "per_day": per_day,
        }

    def _summary(self, days: Optional[int], now=None) -> dict:
        cs = self.calls(days, now)
        n = len(cs)
        reached = [c for c in cs if c["line_result"] in ("answered", "browser")]
        turns = [t for c in cs for t in c["turns"]]

        def stage(key: str) -> dict:
            vals = [t[key] for t in turns if t.get(key) is not None]
            return {"min": min(vals) if vals else None, "median": m.rnd(m.percentile(vals, 0.5), 3),
                    "p95": m.rnd(m.percentile(vals, 0.95), 3), "max": max(vals) if vals else None, "turns": len(vals)}

        slow = Counter(t.get("slowest_stage") or "Not recorded" for t in turns
                       if (t.get("end_to_end") or 0) >= SLOW_TURN_SECONDS)
        durations = [c["duration_seconds"] for c in reached if c["duration_seconds"] is not None]
        tok = [c["tokens_total"] for c in cs if c["tokens_total"]]
        tps = [t["tokens_per_second"] for t in turns if t.get("tokens_per_second")]
        by_model: dict = defaultdict(list)
        for c in reached:
            by_model[(c["models"].get("llm") or "not recorded", c["served_by"] or "")].append(c)
        errors = Counter(e["stage_label"] for c in cs for e in c["errors"])
        post = [c["post_call_llm"] for c in cs if c["post_call_llm"]]
        post_secs = [p["seconds"] for p in post if p.get("seconds") is not None]
        return {
            "totals": {
                "calls": n, "reached": len(reached),
                "agent_closed": sum(c["agent_closed"] for c in reached),
                "transfer_asked": sum(c["transfer_asked"] for c in cs),
                "transferred": sum(c["transferred"] for c in cs),
                "recorded": sum(c["recorded"] for c in cs),
                "duration_median": m.rnd(m.percentile(durations, 0.5), 1),
                "tokens_per_call": round(sum(tok) / len(tok)) if tok else None,
                "tokens_total": sum(tok),
                "tokens_per_second": m.rnd(sum(tps) / len(tps), 1) if tps else None,
                "repeat_requests": sum(c["repeat_requests"] or 0 for c in cs),
                "turns": len(turns),
                "calls_with_errors": sum(1 for c in cs if c["error_count"]),
                "errors": sum(c["error_count"] for c in cs),
                "interruptions": sum(c["interruptions"] or 0 for c in cs),
                "processing": self.processing(),
                "post_call_checks": len(post),
                "post_call_failed": sum(1 for p in post if not p.get("ok")),
                "post_call_seconds_median": m.rnd(m.percentile(post_secs, 0.5), 2),
                "post_call_tokens_per_call": round(sum((p.get("prompt_tokens") or 0) + (p.get("completion_tokens") or 0)
                                                       for p in post) / len(post)) if post else None,
            },
            "errors_by_stage": [{"label": k, "count": v} for k, v in errors.most_common()],
            "stages": [{"key": k, "label": label, **stage(k)} for k, label in STAGES],
            "slow_turn_seconds": SLOW_TURN_SECONDS,
            "slowest_stage": [{"label": k, "count": v} for k, v in slow.most_common()],
            "line_results": [{"key": k, "label": LINE.get(k, k), "count": v}
                             for k, v in Counter(c["line_result"] for c in cs).most_common()],
            "classification": [{"key": k, "label": CLASSIFICATION.get(k, k or "Unknown"), "count": v}
                               for k, v in Counter(c["classification"] for c in cs).most_common()],
            "ended_by": [{"label": k, "count": v} for k, v in Counter(c["ended_by_label"] for c in cs).most_common()],
            "by_model": sorted([{
                "model": mo, "served_by": server or None, "calls": len(v),
                "ttft_median": m.rnd(m.percentile([t["llm_ttft"] for c in v for t in c["turns"]
                                                   if t.get("llm_ttft") is not None], 0.5), 3),
                "e2e_median": m.rnd(m.percentile([t["end_to_end"] for c in v for t in c["turns"]
                                                  if t.get("end_to_end") is not None], 0.5), 3),
                "tokens_per_call": round(sum(c["tokens_total"] or 0 for c in v) / len(v)),
                "qualified": sum(1 for c in v if c["classification"] == "qualified"),
                "calls_with_errors": sum(1 for c in v if c["error_count"]),
            } for (mo, server), v in by_model.items()], key=lambda x: -x["calls"]),
            "per_day": m.day_series(cs, self.tz, days, now=now),
            "cost": self.cost(cs, days, now),
            "heatmap": m.heatmap(cs, self.tz),
            "calls": [public_call(c, full=False) for c in cs[:100]],
            "speech": self.speech_latest(),
            "status": self.status(),
        }

    # ---------- busy hours and bottlenecks ----------
    def per_day(self, days: Optional[int], now=None) -> list:
        return self._remember("per_day", (days,), now,
                              lambda: m.day_series(self.calls(days, now), self.tz, days, now=now))

    def hours(self, days: Optional[int], day: Optional[str] = None, now=None) -> dict:
        return self._remember("hours", (days, day), now, lambda: self._hours(days, day, now))

    def _hours(self, days: Optional[int], day: Optional[str] = None, now=None) -> dict:
        """Per hour of the day (local time): how many calls, how many at once, where the caller's wait goes,
        tokens and call length. `day` (YYYY-MM-DD) narrows it to one day. A turn counts in its call's start hour."""
        in_range = self.calls(days, now)
        days_with_calls = Counter(c["ts"].astimezone(self.tz).date().isoformat() for c in in_range)
        cs = [c for c in in_range if not day or c["ts"].astimezone(self.tz).date().isoformat() == day]
        by_hour: dict = defaultdict(list)
        for c in cs:
            by_hour[c["ts"].astimezone(self.tz).hour].append(c)

        # most calls in progress at the same moment, per hour (a call with no end counts as one minute)
        spans = [(c["ts"], c["ended_at"] if c["ended_at"] and c["ended_at"] > c["ts"] else c["ts"] + timedelta(minutes=1))
                 for c in cs]
        moments = sorted([(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans], key=lambda x: (x[0], x[1]))
        at_once: Counter = Counter()
        live = 0
        for when, step in moments:
            live += step
            if step > 0:
                h = when.astimezone(self.tz).hour
                at_once[h] = max(at_once[h], live)

        def med(vals):
            return m.rnd(m.percentile([v for v in vals if v is not None], 0.5), 3)

        rows = []
        for h in range(24):
            hc = by_hour.get(h, [])
            turns = [t for c in hc for t in c["turns"]]
            stages = {k: med(t.get(k) for t in turns) for k in ("eou", "llm_ttft", "tts_ttfb")}
            known = {k: v for k, v in stages.items() if v is not None}
            rows.append({
                "hour": h, "calls": len(hc), "at_once": at_once.get(h, 0), "turns": len(turns),
                "wait_median": med(t.get("end_to_end") for t in turns),
                "wait_p95": m.rnd(m.percentile([t["end_to_end"] for t in turns if t.get("end_to_end") is not None], 0.95), 3),
                "stages": stages, "bottleneck": max(known, key=known.get) if known else None,
                "tokens": sum(c["tokens_total"] or 0 for c in hc),
                "duration_median": m.rnd(m.percentile([c["duration_seconds"] for c in hc
                                                       if c["duration_seconds"] is not None], 0.5), 1),
                "slow_turns": sum(1 for t in turns if (t.get("end_to_end") or 0) >= SLOW_TURN_SECONDS),
            })

        # busy hours (the top quarter by calls) against the rest: does the wait grow with load?
        active = sorted((r for r in rows if r["calls"]), key=lambda r: -r["calls"])
        busy_n = max(1, len(active) // 4) if len(active) >= 2 else 0
        busy, quiet = active[:busy_n], active[busy_n:]

        def wait_of(rs):
            return med(t.get("end_to_end") for r in rs for c in by_hour[r["hour"]] for t in c["turns"])

        load = None
        if busy and quiet:
            load = {"busy_hours": [r["hour"] for r in busy], "busy_wait_median": wait_of(busy),
                    "quiet_wait_median": wait_of(quiet)}
            if load["busy_wait_median"] is None or load["quiet_wait_median"] is None:
                load = None
        turns_all = [t for c in cs for t in c["turns"]]
        slow = Counter(t.get("slowest_stage") or "Not recorded" for t in turns_all
                       if (t.get("end_to_end") or 0) >= SLOW_TURN_SECONDS)
        slow_total = sum(slow.values())
        peak = max(rows, key=lambda r: r["calls"]) if cs else None
        return {
            "day": day, "calls": len(cs), "slow_turn_seconds": SLOW_TURN_SECONDS,
            "days": [{"date": d, "calls": n} for d, n in sorted(days_with_calls.items(), reverse=True)],
            "hours": rows,
            "stage_labels": {"eou": "Speech-to-text", "llm_ttft": "LLM first token", "tts_ttfb": "Text-to-speech"},
            "slow_reasons": [{"label": k, "count": v, "share": m.rnd(v / slow_total, 3)} for k, v in slow.most_common()],
            "peak": {"hour": peak["hour"], "calls": peak["calls"]} if peak and peak["calls"] else None,
            "max_at_once": max(at_once.values()) if at_once else 0,
            "load": load,
            "heatmap": m.heatmap(in_range, self.tz),
        }

    # ---------- speech accuracy ----------
    def speech_runs(self) -> list:
        out = []
        for rid, raw in self._read_dir(self.evals_dir):
            out.append({"id": rid, "run_at": raw.get("run_at"), "mode": raw.get("mode"), "language": raw.get("language"),
                        "models": raw.get("models") or {}, "summary": raw.get("summary") or {}})
        return sorted(out, key=lambda r: r["run_at"] or "", reverse=True)

    def speech_latest(self) -> dict:
        runs = self.speech_runs()
        latest = {}
        for r in runs:
            latest.setdefault(r["mode"], r)
        return {"round_trip": latest.get("round_trip"), "recordings": latest.get("recordings"), "runs": len(runs)}

    def speech_run(self, run_id: str) -> Optional[dict]:
        if not run_id or not _SAFE.match(run_id):
            return None
        raw = _load(os.path.join(self.evals_dir, run_id + ".json"))
        if not raw:
            return None
        items = raw.get("items") or []
        return {"id": run_id, "run_at": raw.get("run_at"), "mode": raw.get("mode"), "language": raw.get("language"),
                "models": raw.get("models") or {}, "summary": raw.get("summary") or {},
                "items": sorted(items, key=lambda i: -(i.get("wer") or 0))}
