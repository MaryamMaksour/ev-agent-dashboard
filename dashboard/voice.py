"""Reads what the voice agent test (voice-agent-llm-test) already saves. READ-ONLY.

  runs/<YYYYMMDD-HHMMSS>/<model>/<mode>/logs/<scenario>.jsonl   one test call: header, lead/agent lines,
                                                                 tool calls, notes, footer (passed, wrong fields)
  runs/chat/<name>.jsonl                                         a manual chat (python chat.py)

Every number here is worked out from those JSONL lines, using the same rules as the test's own report.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from typing import Optional

from . import metrics as m

# voice-agent-llm-test llmtest/evaluate.py: 8 checked fields, and the "good enough" targets
FIELD_KEYS = ["status", "budget_aed", "preferred_area", "property_type", "timeline",
              "interest_level", "callback_at", "do_not_call"]
TARGET_FIELD_ACCURACY = 0.95
TARGET_PASS_RATE = 0.90
REJECTED_PREFIXES = ("Not saved", "Unknown tool", "Not ended")
_RUN_ID = re.compile(r"^\d{8}-\d{6}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._\-/]+$")


def _note_kind(text: str) -> str:
    if text.startswith("guard:"):
        return "guard"
    if text.startswith("rules set:"):
        return "rules"
    if text.startswith("post-call check:"):
        return "post_call_fill" if "=" in text else "post_call"
    if text.startswith("ERROR"):
        return "error"
    if text.startswith("stopped"):
        return "stopped"
    return "note"


def parse_call(path: str, call_id: str, naive_tz) -> dict:
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    header = next((e for e in events if e.get("type") == "header"), {})
    footer = next((e for e in events if e.get("type") == "footer"), None)
    agent = [e for e in events if e.get("type") == "agent"]
    tools = [e for e in events if e.get("type") == "tool"]
    notes = [e for e in events if e.get("type") == "note"]
    kinds = Counter(_note_kind(n.get("text", "")) for n in notes)
    secs = [float(e.get("model_seconds") or 0) for e in agent]
    times = [t for t in (m.parse_ts(e.get("time"), naive_tz) for e in events) if t]
    wrong = list(footer.get("wrong_fields") or []) if footer else []
    try:
        final_row = json.loads(footer["final_crm_row"]) if footer and footer.get("final_crm_row") else None
    except (TypeError, ValueError):
        final_row = None
    is_test = bool(header.get("scenario"))
    run_id = call_id.split("/", 1)[0]
    return {
        "id": call_id,
        "run_id": run_id,
        "kind": "test" if is_test else "chat",
        "model": header.get("model", "?"),
        "mode": header.get("mode", "chat"),
        "scenario": header.get("scenario") or os.path.basename(call_id),
        "tests": header.get("tests", ""),
        "lead": header.get("lead", ""),
        "ts": times[0] if times else None,
        "ended_at": times[-1] if times else None,
        "ended_by": footer.get("ended_by") if footer else "incomplete",
        "passed": footer.get("passed") if footer and is_test else None,
        "wrong_fields": wrong,
        "field_accuracy": (len(FIELD_KEYS) - len(wrong)) / len(FIELD_KEYS) if footer and is_test else None,
        "agent_turns": len(agent),
        "lead_turns": sum(1 for e in events if e.get("type") == "lead"),
        "model_seconds": round(sum(secs), 2),
        "seconds_per_reply": round(sum(secs) / len(secs), 2) if secs else None,
        "slowest_reply": round(max(secs), 2) if secs else None,
        "tool_calls": len(tools),
        "tool_rejected": sum(1 for t in tools if str(t.get("result", "")).startswith(REJECTED_PREFIXES)),
        "tool_names": Counter(t.get("tool", "?") for t in tools),
        "guards": kinds.get("guard", 0),
        "rules": kinds.get("rules", 0),
        "post_call_fills": kinds.get("post_call_fill", 0),
        "errors": kinds.get("error", 0),
        "no_reply_turns": sum(1 for e in agent if e.get("text") == "(no spoken reply)"),
        "final_row": final_row,
        "events": events,
    }


def public_call(c: dict, *, with_events: bool, show_text: bool) -> dict:
    out = {k: v for k, v in c.items() if k not in ("events", "ts", "ended_at", "tool_names", "final_row")}
    out["ts"] = m.iso(c["ts"])
    out["ended_at"] = m.iso(c["ended_at"])
    out["tool_names"] = dict(c["tool_names"])
    if with_events:
        out["final_row"] = c["final_row"] if show_text else None
        out["events"] = c["events"] if show_text else None
    return out


class VoiceTestSource:
    def __init__(self, runs_dir: str, tz, naive_tz) -> None:
        self.runs_dir = runs_dir
        self.tz = tz
        self.naive_tz = naive_tz
        self._cache: dict = {}

    def _files(self) -> list:
        """(call_id, path) for every call log under runs/."""
        out = []
        if not os.path.isdir(self.runs_dir):
            return out
        for root, _dirs, files in os.walk(self.runs_dir):
            for name in files:
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(root, name)
                rel = os.path.relpath(path, self.runs_dir)[:-len(".jsonl")].replace(os.sep, "/")
                out.append((rel, path))
        return sorted(out)

    def _load(self, call_id: str, path: str) -> Optional[dict]:
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        hit = self._cache.get(call_id)
        if hit and hit[0] == key:
            return hit[1]
        try:
            call = parse_call(path, call_id, self.naive_tz)
        except OSError:
            return None
        self._cache[call_id] = (key, call)
        return call

    def calls(self, run_id: Optional[str] = None) -> list:
        out = []
        for cid, path in self._files():
            if run_id and cid.split("/", 1)[0] != run_id:
                continue
            c = self._load(cid, path)
            if c:
                out.append(c)
        return out

    def call(self, call_id: str) -> Optional[dict]:
        if not call_id or ".." in call_id or not _SAFE_ID.match(call_id):
            return None
        root = os.path.realpath(self.runs_dir)
        path = os.path.realpath(os.path.join(root, call_id + ".jsonl"))
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            return None
        return self._load(call_id, path)

    # ---------- dashboard views ----------
    def status(self) -> dict:
        runs = self.runs()
        return {"runs_dir": self.runs_dir, "found": os.path.isdir(self.runs_dir), "runs": len(runs),
                "last_run": runs[0]["started"] if runs else None}

    def runs(self) -> list:
        by_run: dict = defaultdict(list)
        for c in self.calls():
            by_run[c["run_id"]].append(c)
        out = []
        for rid, cs in by_run.items():
            tests = [c for c in cs if c["kind"] == "test"]
            started = min((c["ts"] for c in cs if c["ts"]), default=None)
            if _RUN_ID.match(rid):
                label = f"Test run {rid[:4]}-{rid[4:6]}-{rid[6:8]} {rid[9:11]}:{rid[11:13]}"
            elif rid == "chat":
                label = "Manual chats (chat.py)"
            else:
                label = rid
            out.append({
                "id": rid, "label": label, "started": m.iso(started), "calls": len(cs),
                "models": sorted({c["model"] for c in cs}), "modes": sorted({c["mode"] for c in cs}),
                "passed": sum(1 for c in tests if c["passed"]), "tests": len(tests),
                "kind": "test" if tests else "chat",
            })
        out.sort(key=lambda r: r["started"] or "", reverse=True)
        return out

    def run_detail(self, run_id: str) -> Optional[dict]:
        cs = self.calls(run_id)
        if not cs:
            return None
        by_model: dict = defaultdict(list)
        for c in cs:
            by_model[c["model"]].append(c)
        models = []
        for model, mc in sorted(by_model.items()):
            tests = [c for c in mc if c["kind"] == "test" and c["passed"] is not None]
            acc = (sum(c["field_accuracy"] for c in tests) / len(tests)) if tests else None
            pass_rate = (sum(1 for c in tests if c["passed"]) / len(tests)) if tests else None
            replies = [s for c in mc for s in _reply_seconds(c)]
            verdict = None
            if tests:
                verdict = "GOOD ENOUGH" if acc >= TARGET_FIELD_ACCURACY and pass_rate >= TARGET_PASS_RATE else "NOT ENOUGH"
            models.append({
                "model": model, "calls": len(mc), "tests": len(tests),
                "passed": sum(1 for c in tests if c["passed"]),
                "pass_rate": m.rnd(pass_rate, 3), "field_accuracy": m.rnd(acc, 3), "verdict": verdict,
                "ended_properly": sum(1 for c in mc if c["ended_by"] == "end_call"),
                "seconds_per_reply": m.rnd(sum(replies) / len(replies), 2) if replies else None,
                "seconds_p95": m.rnd(m.percentile(replies, 0.95), 2),
                "tool_calls": sum(c["tool_calls"] for c in mc),
                "tool_rejected": sum(c["tool_rejected"] for c in mc),
                "guards": sum(c["guards"] for c in mc),
                "post_call_fills": sum(c["post_call_fills"] for c in mc),
                "errors": sum(c["errors"] for c in mc) + sum(1 for c in mc if c["ended_by"] == "error"),
            })
        wrong: dict = defaultdict(Counter)
        ended: dict = defaultdict(Counter)
        for c in cs:
            for f in c["wrong_fields"]:
                wrong[f][c["model"]] += 1
            ended[c["ended_by"]][c["model"]] += 1
        columns = sorted({(c["model"], c["mode"]) for c in cs})
        scenarios = sorted({c["scenario"] for c in cs})
        cells = {(c["scenario"], c["model"], c["mode"]): c for c in cs}
        matrix = [{
            "scenario": sc,
            "tests": next((c["tests"] for c in cs if c["scenario"] == sc), ""),
            "cells": [_cell(cells.get((sc, mo, md))) for mo, md in columns],
        } for sc in scenarios]
        return {
            "run": next((r for r in self.runs() if r["id"] == run_id), {"id": run_id}),
            "models": models,
            "targets": {"field_accuracy": TARGET_FIELD_ACCURACY, "pass_rate": TARGET_PASS_RATE},
            "columns": [{"model": mo, "mode": md} for mo, md in columns],
            "matrix": matrix,
            "wrong_fields": sorted([{"field": f, "by_model": dict(v), "total": sum(v.values())}
                                    for f, v in wrong.items()], key=lambda x: -x["total"]),
            "ended_by": sorted([{"ended_by": k, "by_model": dict(v), "total": sum(v.values())}
                                for k, v in ended.items()], key=lambda x: -x["total"]),
            "calls": [public_call(c, with_events=False, show_text=False)
                      for c in sorted(cs, key=lambda c: (c["model"], c["mode"], c["scenario"]))],
        }

    def summary(self, days: Optional[int], now=None) -> dict:
        start = m.since(days, now)
        cs = [c for c in self.calls() if c["ts"] and (not start or c["ts"] >= start)]
        replies = [s for c in cs for s in _reply_seconds(c)]
        tests = [c for c in cs if c["kind"] == "test" and c["passed"] is not None]
        runs = self.runs()
        latest = next((r for r in runs if r["kind"] == "test"), None)
        latest_detail = self.run_detail(latest["id"]) if latest else None
        return {
            "totals": {
                "calls": len(cs), "tests": len(tests), "chats": sum(1 for c in cs if c["kind"] == "chat"),
                "passed": sum(1 for c in tests if c["passed"]),
                "seconds_per_reply": m.rnd(sum(replies) / len(replies), 2) if replies else None,
                "models": sorted({c["model"] for c in cs}),
            },
            "per_day": m.day_series(cs, self.tz, days, now=now),
            "heatmap": m.heatmap(cs, self.tz),
            "latest_run": latest_detail and {"run": latest_detail["run"], "models": latest_detail["models"]},
            "status": self.status(),
        }


def _reply_seconds(c: dict) -> list:
    return [float(e.get("model_seconds") or 0) for e in c["events"] if e.get("type") == "agent"]


def _cell(c: Optional[dict]) -> Optional[dict]:
    if not c:
        return None
    return {"id": c["id"], "passed": c["passed"], "wrong_fields": c["wrong_fields"], "ended_by": c["ended_by"]}
