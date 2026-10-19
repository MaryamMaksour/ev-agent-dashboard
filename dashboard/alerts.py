"""Voice agent operations: calling-rule compliance, retention (report only), service health, alerts. READ-ONLY.

Reads, next to the calls (VOICE_AGENT_DATA_DIR):
  blocked/<YYYY-MM-DD>.jsonl   dials the agent's calling rules stopped (agent/compliance.py)
  health.json                  the agent's service check, rewritten every minute (agent.check --watch)
  recordings/                  call recordings (only counted and measured, never touched)

Nothing here deletes or moves a file. Retention only says what is older than VOICE_RETENTION_DAYS.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import metrics as m
from .voice_calls import RAW_SUFFIX, VoiceCallSource

RULES = {"daily_limit": "Over the daily limit", "weekly_limit": "Over the weekly limit",
         "blocked_status": "Not interested / wrong number", "do_not_call": "Do not call",
         "outside_hours": "Outside calling hours", "llm_down": "LLM was down"}
SERVICES = {"llm": "LLM", "stt": "Speech-to-text", "tts": "Text-to-speech", "livekit": "LiveKit", "redis": "Redis"}
STOP_CLASSES = {"not_interested", "do_not_call", "wrong_number"}


@dataclass(frozen=True)
class OpsSettings:
    max_calls_per_day: int = 1          # the same limits as the agent's MAX_CALLS_PER_DAY / _WEEK
    max_calls_per_week: int = 2
    retention_days: int = 365
    alert_error_rate: float = 0.10      # share of calls with an error, last 24 h
    alert_wait_seconds: float = 1.5     # typical caller wait, last 24 h
    alert_quiet_hours: float = 6.0      # no new call for this long (0 = off)
    health_stale_seconds: float = 300.0
    stuck_minutes: float = 30.0         # a call still "being processed" after this long


class VoiceOps:
    def __init__(self, source: VoiceCallSource, settings: OpsSettings = OpsSettings()) -> None:
        self.src = source
        self.s = settings
        self.data_dir = source.data_dir
        self.blocked_dir = os.path.join(self.data_dir, "blocked")
        self.health_file = os.path.join(self.data_dir, "health.json")
        self.recordings_dir = os.path.join(self.data_dir, "recordings")
        self._blocked_cache: dict = {}   # path -> ((mtime, size), rows)
        self._sizes: Optional[dict] = None
        self._sizes_at = 0.0
        self._sizes_lock = threading.Lock()
        self._measuring = False

    # ---------- blocked dials ----------
    def blocked(self, days: Optional[int], now=None) -> dict:
        start = m.since(days, now)
        rows = []
        try:
            names = sorted(os.listdir(self.blocked_dir))
        except OSError:
            names = []
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            if start and name[:10] < (start - timedelta(days=1)).strftime("%Y-%m-%d"):
                continue  # whole file is older than the period (file names are UTC dates)
            path = os.path.join(self.blocked_dir, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = (st.st_mtime_ns, st.st_size)
            hit = self._blocked_cache.get(path)
            if not hit or hit[0] != key:
                parsed = []
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            x = json.loads(line)
                        except ValueError:
                            continue
                        ts = m.parse_ts(x.get("ts"))
                        if isinstance(x, dict) and ts:
                            parsed.append({"ts": ts, "lead_id": x.get("lead_id"), "number": x.get("number"),
                                           "rule": x.get("rule") or "unknown", "detail": x.get("detail") or "",
                                           "source": x.get("source")})
                hit = (key, parsed)
                self._blocked_cache[path] = hit
            rows.extend(r for r in hit[1] if not start or r["ts"] >= start)
        rows.sort(key=lambda r: r["ts"], reverse=True)
        by_rule = Counter(r["rule"] for r in rows)
        return {"found": os.path.isdir(self.blocked_dir), "total": len(rows),
                "by_rule": [{"rule": k, "label": RULES.get(k, k), "count": v} for k, v in by_rule.most_common()],
                "recent": [{**r, "ts": m.iso(r["ts"]), "label": RULES.get(r["rule"], r["rule"])} for r in rows[:50]]}

    # ---------- service health ----------
    def health(self, now=None) -> dict:
        now = now or datetime.now(timezone.utc)
        try:
            with open(self.health_file, encoding="utf-8") as f:
                h = json.load(f)
        except (OSError, ValueError):
            return {"found": False, "stale": True, "ts": None, "age_seconds": None, "services": []}
        ts = m.parse_ts(h.get("ts"))
        age = (now - ts).total_seconds() if ts else None
        services = [{"key": k, "label": SERVICES.get(k, k), **(v if isinstance(v, dict) else {})}
                    for k, v in (h.get("services") or {}).items()]
        return {"found": True, "ts": m.iso(ts), "age_seconds": round(age) if age is not None else None,
                "stale": age is None or age > self.s.health_stale_seconds, "services": services}

    # ---------- calling-rule compliance (worked out from the calls themselves) ----------
    def compliance(self, days: Optional[int], now=None) -> dict:
        return self.src._remember("compliance", (days,), now, lambda: self._compliance(days, now))

    def _compliance(self, days: Optional[int], now=None) -> dict:
        now = now or datetime.now(timezone.utc)
        start = m.since(days, now)
        by_lead: dict = defaultdict(list)
        for c in self.src.calls(None):
            by_lead[c["lead_id"]].append(c)
        over_day, over_week, after_stop = [], [], []
        phone_total = answered = recorded = 0
        for lead_id, cs in by_lead.items():
            cs = sorted(cs, key=lambda c: c["ts"])  # oldest first
            phone = [c for c in cs if c["line"]]
            in_period = [c for c in phone if not start or c["ts"] >= start]
            phone_total += len(in_period)
            answered += sum(1 for c in in_period if c["line_result"] == "answered")
            recorded += sum(1 for c in in_period if c["line_result"] == "answered" and c["recorded"])
            per_day = Counter(c["ts"].astimezone(self.src.tz).date() for c in in_period)
            worst_day = max(per_day.values(), default=0)
            if self.s.max_calls_per_day and worst_day > self.s.max_calls_per_day:
                over_day.append({"lead_id": lead_id, "most_in_a_day": worst_day})
            if self.s.max_calls_per_week:
                times = [c["ts"] for c in phone]  # oldest first
                worst, lo = 0, 0
                for i, t in enumerate(times):  # sliding 7-day window ending at each call
                    while times[lo] <= t - timedelta(days=7):
                        lo += 1
                    if not start or t >= start:
                        worst = max(worst, i - lo + 1)
                if worst > self.s.max_calls_per_week:
                    over_week.append({"lead_id": lead_id, "most_in_7_days": worst})
            stopped_at = next((c["ts"] for c in cs if c["classification"] in STOP_CLASSES or c["do_not_call"]), None)
            if stopped_at:
                later = [c for c in in_period if c["ts"] > stopped_at]
                if later:
                    after_stop.append({"lead_id": lead_id, "calls_after": len(later), "last": m.iso(later[-1]["ts"])})
        return {"limits": {"per_day": self.s.max_calls_per_day, "per_week": self.s.max_calls_per_week},
                "phone_calls": phone_total, "answered": answered, "recorded": recorded,
                "over_daily_limit": over_day[:50], "over_daily_count": len(over_day),
                "over_weekly_limit": over_week[:50], "over_weekly_count": len(over_week),
                "called_after_stop": after_stop[:50], "called_after_stop_count": len(after_stop),
                "breaches": len(over_day) + len(over_week) + len(after_stop)}

    # ---------- retention: report only, nothing is deleted ----------
    def _measure(self) -> None:
        def folder(path: str) -> tuple:
            n = size = 0
            oldest = None
            for root, _dirs, files in os.walk(path):
                for f in files:
                    try:
                        st = os.stat(os.path.join(root, f))
                    except OSError:
                        continue
                    n += 1
                    size += st.st_size
                    oldest = st.st_mtime if oldest is None else min(oldest, st.st_mtime)
            return n, size, oldest

        cutoff = time.time() - self.s.retention_days * 86400
        rec_old = 0
        rec_files, rec_size, rec_oldest = folder(self.recordings_dir)
        if rec_files:
            for root, _dirs, files in os.walk(self.recordings_dir):
                for f in files:
                    try:
                        rec_old += os.stat(os.path.join(root, f)).st_mtime < cutoff
                    except OSError:
                        pass
        calls_files, calls_size, _ = folder(self.src.calls_dir)
        with self._sizes_lock:
            self._sizes = {"calls_files": calls_files, "calls_bytes": calls_size,
                           "recordings": rec_files, "recordings_bytes": rec_size, "recordings_past_limit": rec_old,
                           "oldest_recording": m.iso(datetime.fromtimestamp(rec_oldest, timezone.utc)) if rec_oldest else None,
                           "measured_at": m.iso(datetime.now(timezone.utc))}
            self._sizes_at = time.monotonic()

    def retention(self, now=None, wait: bool = False) -> dict:
        """Counts and sizes are measured in the background at most every 10 minutes (the folders can hold
        hundreds of thousands of files); the first answer may say "measuring"."""
        now = now or datetime.now(timezone.utc)
        due = self._sizes is None or time.monotonic() - self._sizes_at > 600
        if due and wait:
            self._measure()
        elif due and not self._measuring:
            self._measuring = True  # one measurement at a time

            def run() -> None:
                try:
                    self._measure()
                finally:
                    self._measuring = False

            threading.Thread(target=run, daemon=True).start()
        calls = self.src.calls(None)
        limit = now - timedelta(days=self.s.retention_days)
        past = [c for c in calls if c["ts"] < limit]
        return {"days": self.s.retention_days, "oldest_call": m.iso(calls[-1]["ts"]) if calls else None,
                "calls": len(calls), "calls_past_limit": len(past), "sizes": self._sizes,
                "note": "Report only: nothing is deleted. Decide with legal what to keep, then archive by hand."}

    # ---------- alerts ----------
    def alerts(self, now=None) -> dict:
        """Worked out again at most every VOICE_CACHE_SECONDS (every page asks for them)."""
        return self.src._remember("alerts", (), now, lambda: self._alerts(now))

    def _alerts(self, given=None) -> dict:
        now = given or datetime.now(timezone.utc)
        out = []

        def add(key, level, title, detail, action, since=None):
            out.append({"key": key, "level": level, "title": title, "detail": detail, "action": action,
                        "since": m.iso(since) if isinstance(since, datetime) else since})

        found = os.path.isdir(self.src.calls_dir)
        if not found:
            return {"alerts": [], "crit": 0, "warn": 0, "found": False}
        day = [c for c in self.src.calls(None) if c["ts"] >= now - timedelta(days=1)]
        h = self.health(now)
        if not h["found"]:
            add("health_missing", "warn", "No service health file",
                "data/health.json is missing, so the dashboard can't tell whether the LLM, STT and TTS are up.",
                "Deploy the latest voice agent: its container writes the file every minute (agent.check --watch).")
        elif h["stale"]:
            add("health_stale", "crit", "Service check stopped",
                f"data/health.json was last written {h['age_seconds'] // 60 if h['age_seconds'] else '?'} minutes ago.",
                "Check that the agent container is running: docker compose ps, then logs agent.", h["ts"])
        else:
            for sv in h["services"]:
                if sv.get("ok") is False:
                    add(f"down_{sv['key']}", "crit", f"{sv['label']} is down", sv.get("error") or "The check failed.",
                        "Run python -m agent.check in the agent container for the fix.", h["ts"])
        with_errors = sum(1 for c in day if c["error_count"])
        if len(day) >= 5 and with_errors / len(day) > self.s.alert_error_rate:
            add("error_rate", "crit", "Many calls with errors",
                f"{with_errors} of {len(day)} calls in the last 24 hours had an error ({with_errors / len(day):.0%}).",
                "Open Voice agent calls → Quality → Errors to see which stage fails.")
        waits = [t["end_to_end"] for c in day for t in c["turns"] if t.get("end_to_end") is not None]
        typical = m.percentile(waits, 0.5)
        if len(waits) >= 20 and typical and typical > self.s.alert_wait_seconds:
            add("slow", "warn", "Callers are waiting too long",
                f"Typical wait in the last 24 hours is {typical:.1f} s (target {self.s.alert_wait_seconds} s).",
                "Open Busy hours to see which step is the bottleneck and when.")
        last = self.src.calls(None)[:1]
        if self.s.alert_quiet_hours and last and now - last[0]["ts"] > timedelta(hours=self.s.alert_quiet_hours):
            add("quiet", "warn", "No new calls",
                f"The last call was {(now - last[0]['ts']).total_seconds() / 3600:.0f} hours ago.",
                "Check that calls are being started (dialler, 3CX trunk) and that the agent worker is running.",
                last[0]["ts"])
        stuck = 0
        try:
            names = os.listdir(self.src.calls_dir)
            for n in names:
                if n.endswith(RAW_SUFFIX) and n[: -len(RAW_SUFFIX)] + ".json" not in names:
                    age = time.time() - os.stat(os.path.join(self.src.calls_dir, n)).st_mtime
                    stuck += age > self.s.stuck_minutes * 60
        except OSError:
            pass
        if stuck:
            add("stuck", "warn", "Calls stuck in processing",
                f"{stuck} call(s) have waited more than {self.s.stuck_minutes:.0f} minutes for the post-call check.",
                "Run python -m agent.postcall in the agent container.")
        failed = [c for c in day if c["ended_by"] == "llm_failure"]
        if failed:
            add("llm_failure", "crit", "Calls stopped by an LLM failure",
                f"{len(failed)} call(s) in the last 24 hours. The agent apologised and booked callbacks.",
                "Check vLLM (docker compose logs vllm); see Follow-up for the clients to call back.", failed[0]["ts"])
        b = self.blocked(1, now)
        llm_down = next((x["count"] for x in b["by_rule"] if x["rule"] == "llm_down"), 0)
        if llm_down:
            add("llm_down", "crit", "Dials skipped: LLM down",
                f"{llm_down} dial(s) in the last 24 hours were not made because the LLM wasn't answering.",
                "Check vLLM; the clients were not called.")
        comp = self.compliance(30, given)
        if comp["breaches"]:
            add("compliance", "crit", "Calling rules broken",
                f"Last 30 days: {comp['over_daily_count']} client(s) over {comp['limits']['per_day']} call a day, "
                f"{comp['over_weekly_count']} over {comp['limits']['per_week']} a week, "
                f"{comp['called_after_stop_count']} called after not-interested / do-not-call / wrong number.",
                "Open Quality → Compliance for the clients; check the agent's MAX_CALLS_* settings.")
        r = self.retention(now)
        if r["calls_past_limit"] or ((r["sizes"] or {}).get("recordings_past_limit")):
            add("retention", "warn", "Data past the retention period",
                f"{r['calls_past_limit']} call record(s) and {(r['sizes'] or {}).get('recordings_past_limit', 0)} "
                f"recording(s) are older than {r['days']} days. Nothing has been deleted.",
                "Review with legal what to keep; archive by hand if needed.")
        overdue = self.src.follow_up(now=given)["overdue"]
        if overdue:
            add("overdue", "warn", "Callbacks overdue", f"{overdue} client(s) are waiting for a promised callback.",
                "Open Voice agent calls → Follow-up.")
        out.sort(key=lambda a: a["level"] != "crit")
        return {"alerts": out, "crit": sum(a["level"] == "crit" for a in out),
                "warn": sum(a["level"] == "warn" for a in out), "found": True}
