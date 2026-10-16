"""Reads what the real voice agent (ev-voice-agent, LiveKit) saves. READ-ONLY.

  <data>/calls/<call>.json         one call: transcript, CRM actions, tool calls, per-turn timings and tokens,
                                   phone line result, recording, transfer, classification
  <data>/speech_evals/<run>.json   speech-to-text / text-to-speech accuracy runs (python -m evals.run_speech)

Older call records (before the agent recorded these fields) still load; missing values show as unknown.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
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
SLOW_TURN_SECONDS = 1.5
_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


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
        "models": raw.get("models") or {},
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
    }


TEXT_FIELDS = ("transcript", "tool_calls", "actions", "summary", "filled_after_call", "disagreements")


def public_call(c: dict, *, full: bool, show_text: bool) -> dict:
    hidden = {"ts", "ended_at", "metrics", "turns", *TEXT_FIELDS}
    out = {k: v for k, v in c.items() if k not in hidden}
    out["ts"], out["ended_at"] = m.iso(c["ts"]), m.iso(c["ended_at"])
    out["tool_call_count"] = len(c["tool_calls"])
    if c["transfer"] and not show_text:
        out["transfer"] = {k: v for k, v in c["transfer"].items() if k != "reason"}
    if full:
        out["turns"] = c["turns"]  # timings and tokens only
        for k in TEXT_FIELDS:
            out[k] = c[k] if show_text else None
    return out


class VoiceCallSource:
    def __init__(self, data_dir: str, tz) -> None:
        self.data_dir = data_dir
        self.calls_dir = os.path.join(data_dir, "calls")
        self.evals_dir = os.path.join(data_dir, "speech_evals")
        self.tz = tz
        self._cache: dict = {}

    def _read_dir(self, folder: str) -> list:
        out = []
        if not os.path.isdir(folder):
            return out
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
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

    def calls(self, days: Optional[int] = None, now=None) -> list:
        start = m.since(days, now)
        out = [parse_call(raw, cid) for cid, raw in self._read_dir(self.calls_dir)]
        out = [c for c in out if c["ts"] and (not start or c["ts"] >= start)]
        return sorted(out, key=lambda c: c["ts"], reverse=True)

    def call(self, call_id: str) -> Optional[dict]:
        if not call_id or not _SAFE.match(call_id):
            return None
        raw = _load(os.path.join(self.calls_dir, call_id + ".json"))
        return parse_call(raw, call_id) if raw else None

    def status(self) -> dict:
        calls = self.calls()
        return {"data_dir": self.data_dir, "found": os.path.isdir(self.calls_dir), "calls": len(calls),
                "last_call": m.iso(calls[0]["ts"]) if calls else None,
                "speech_runs": len(self._read_dir(self.evals_dir))}

    def summary(self, days: Optional[int], now=None) -> dict:
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
            by_model[c["models"].get("llm") or "not recorded"].append(c)
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
            },
            "stages": [{"key": k, "label": label, **stage(k)} for k, label in STAGES],
            "slow_turn_seconds": SLOW_TURN_SECONDS,
            "slowest_stage": [{"label": k, "count": v} for k, v in slow.most_common()],
            "line_results": [{"key": k, "label": LINE.get(k, k), "count": v}
                             for k, v in Counter(c["line_result"] for c in cs).most_common()],
            "classification": [{"label": k, "count": v}
                               for k, v in Counter(c["classification_label"] for c in cs).most_common()],
            "ended_by": [{"label": k, "count": v} for k, v in Counter(c["ended_by_label"] for c in cs).most_common()],
            "by_model": sorted([{
                "model": mo, "calls": len(v),
                "ttft_median": m.rnd(m.percentile([t["llm_ttft"] for c in v for t in c["turns"]
                                                   if t.get("llm_ttft") is not None], 0.5), 3),
                "e2e_median": m.rnd(m.percentile([t["end_to_end"] for c in v for t in c["turns"]
                                                  if t.get("end_to_end") is not None], 0.5), 3),
                "tokens_per_call": round(sum(c["tokens_total"] or 0 for c in v) / len(v)),
                "qualified": sum(1 for c in v if c["classification"] == "qualified"),
            } for mo, v in by_model.items()], key=lambda x: -x["calls"]),
            "per_day": m.day_series(cs, self.tz, days, now=now),
            "heatmap": m.heatmap(cs, self.tz),
            "calls": [public_call(c, full=False, show_text=False) for c in cs[:100]],
            "speech": self.speech_latest(),
            "status": self.status(),
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

    def speech_run(self, run_id: str, *, show_text: bool) -> Optional[dict]:
        if not run_id or not _SAFE.match(run_id):
            return None
        raw = _load(os.path.join(self.evals_dir, run_id + ".json"))
        if not raw:
            return None
        items = raw.get("items") or []
        if not show_text:
            items = [{k: v for k, v in i.items() if k not in ("reference", "transcript", "missed_key_terms")}
                     for i in items]
        return {"id": run_id, "run_at": raw.get("run_at"), "mode": raw.get("mode"), "language": raw.get("language"),
                "models": raw.get("models") or {}, "summary": raw.get("summary") or {},
                "items": sorted(items, key=lambda i: -(i.get("wer") or 0))}
