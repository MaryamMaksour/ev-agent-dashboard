"""Builds tiny agent-like data folders (same formats as ev-proto and voice-agent-llm-test) for the tests."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)  # 12:00 UAE, a Friday


def make_evproto(folder: str) -> None:
    os.makedirs(folder, exist_ok=True)
    a = sqlite3.connect(os.path.join(folder, "audit.db"))
    a.executescript("""
    CREATE TABLE qa_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, user_id TEXT, name TEXT, role TEXT,
      question TEXT, answer TEXT, sources TEXT, model TEXT, cost_usd REAL, elapsed_ms INTEGER, ip TEXT);
    CREATE TABLE auth_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, identity TEXT, success INTEGER,
      ip TEXT, reason TEXT);
    """)
    rows = [
        # (minutes before NOW, user, question, answer, sources, model, cost, ms)
        (60, "sara", "How many active deals in LUME?", "LUME has 42 active deals.", [{"module": "Deals", "id": "1"}], "claude-haiku-4-5", 0.004, 2100),
        (58, "sara", "how many active deals in LUME please", "LUME has 42 active deals.", [{"module": "Deals", "id": "1"}], "claude-haiku-4-5", 0.004, 2000),
        (50, "omar", "Deals by booking date", "I'm not able to pull that up right now. Here are a few things I can help with instead:", [], "claude-sonnet-4-6", 0.02, 12500),
        (49, "omar", "Are you sure?", "Yes.", [], "claude-haiku-4-5", 0.002, 900),
        (40, "lina", "List every EOI", "I couldn't finish that one — it may be too broad.", [], "claude-sonnet-4-6", 0.03, 15000),
        (30, "lina", "Weather?", "Sorry, that's outside what I can help with. I only answer questions about EV's CRM records.", [], "claude-haiku-4-5", 0.001, 700),
        (20, "rami", "مرحبا", "أهلاً! كيف أساعدك؟", [], "claude-haiku-4-5", 0.001, 800),
        (60 * 24 * 40, "rami", "old question", "old answer", [{"module": "Leads", "id": "9"}], "claude-haiku-4-5", 0.004, 2000),
    ]
    for mins, uid, q, ans, src, model, cost, ms in rows:
        ts = (NOW - timedelta(minutes=mins)).isoformat()
        a.execute("INSERT INTO qa_log (ts,user_id,name,role,question,answer,sources,model,cost_usd,elapsed_ms,ip) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (ts, uid, uid.title(), "user", q, ans, json.dumps(src), model, cost, ms, "10.0.0.5"))
    a.executescript("""
    CREATE TABLE error_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL, source TEXT,
      message TEXT, detail TEXT, user_id TEXT, name TEXT, question TEXT, path TEXT, ip TEXT);
    """)
    for mins, kind, source, msg, detail, uid, q in (
        (50, "tool_error", "crm_search", "INVALID_QUERY (field Booking_Date not searchable)",
         json.dumps({"system": "Zoho CRM", "input": {"module": "Deals"}}), "omar", "Deals by booking date"),
        (40, "round_limit", "orchestrator", "No usable answer after 10 research rounds", "", "lina", "List every EOI"),
        (39, "claude_api", "chat", "RateLimitError: 429", "Traceback ...", "lina", "List every EOI"),
        (60 * 24 * 40, "server", "/conversations", "OperationalError: database is locked", "", "", ""),
    ):
        a.execute("INSERT INTO error_log (ts,kind,source,message,detail,user_id,name,question,path,ip) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  ((NOW - timedelta(minutes=mins)).isoformat(), kind, source, msg, detail, uid, uid.title(), q, "/chat", "10.0.0.5"))
    for mins, ident, ok in ((70, "sara", 1), (69, "+971500000000", 0), (68, "+971500000000", 0)):
        a.execute("INSERT INTO auth_log (ts,identity,success,ip,reason) VALUES (?,?,?,?,?)",
                  ((NOW - timedelta(minutes=mins)).isoformat(), ident, ok, "203.0.113.7", "" if ok else "bad_code"))
    a.commit()
    a.close()
    c = sqlite3.connect(os.path.join(folder, "conversations.db"))
    c.executescript("""
    CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'New chat',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, role TEXT NOT NULL,
      content TEXT NOT NULL, created_at TEXT NOT NULL);
    """)
    ts = (NOW - timedelta(minutes=60)).isoformat()
    c.execute("INSERT INTO conversations (user_id,title,created_at,updated_at) VALUES ('sara','t',?,?)", (ts, ts))
    for role in ("user", "assistant", "user", "assistant"):
        c.execute("INSERT INTO messages (conversation_id,role,content,created_at) VALUES (1,?,?,?)", (role, "x", ts))
    c.execute("INSERT INTO conversations (user_id,title,created_at,updated_at) VALUES ('omar','t',?,?)", (ts, ts))
    c.execute("INSERT INTO messages (conversation_id,role,content,created_at) VALUES (2,'user','x',?)", (ts,))
    c.commit()
    c.close()


def write_call(path: str, events: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def make_voice_runs(folder: str) -> None:
    run = os.path.join(folder, "20261015-101500")
    write_call(os.path.join(run, "qwen3_8b", "scripted", "logs", "01_qualified_full.jsonl"), [
        {"time": "2026-10-15T10:15:00", "type": "header", "model": "qwen3:8b", "mode": "scripted", "scenario": "01_qualified_full", "tests": "Qualified", "lead": "L001 Sarah"},
        {"time": "2026-10-15T10:15:01", "type": "lead", "text": "Hello?"},
        {"time": "2026-10-15T10:15:03", "type": "agent", "text": "Hi!", "model_seconds": 2.0},
        {"time": "2026-10-15T10:15:05", "type": "tool", "tool": "update_lead", "args": {"property_type": "castle"}, "result": "Not saved: bad type"},
        {"time": "2026-10-15T10:15:06", "type": "tool", "tool": "end_call", "args": {}, "result": "Call ended."},
        {"time": "2026-10-15T10:15:06", "type": "note", "text": "guard: removed 'Prices start at 1M.'"},
        {"time": "2026-10-15T10:15:07", "type": "agent", "text": "Bye", "model_seconds": 4.0},
        {"time": "2026-10-15T10:15:08", "type": "note", "text": "post-call check: filled timeline=6 months"},
        {"time": "2026-10-15T10:15:09", "type": "footer", "passed": False, "ended_by": "end_call", "final_crm_row": "{\"status\": \"interested\"}", "wrong_fields": ["status", "budget_aed"]},
    ])
    write_call(os.path.join(run, "qwen3_14b", "scripted", "logs", "01_qualified_full.jsonl"), [
        {"time": "2026-10-15T10:20:00", "type": "header", "model": "qwen3:14b", "mode": "scripted", "scenario": "01_qualified_full", "tests": "Qualified", "lead": "L001 Sarah"},
        {"time": "2026-10-15T10:20:03", "type": "agent", "text": "Hi!", "model_seconds": 3.0},
        {"time": "2026-10-15T10:20:09", "type": "footer", "passed": True, "ended_by": "end_call", "final_crm_row": "{}", "wrong_fields": []},
    ])
    # new log format (voice-agent-llm-test after "Record what the dashboard needs"): timings, tools ok, classification
    rnd = lambda secs, load, read, write, tin, tout, tools=(), think=0: {  # noqa: E731
        "seconds": secs, "load_seconds": load, "prompt_seconds": read, "generate_seconds": write,
        "prompt_tokens": tin, "output_tokens": tout, "tool_calls": list(tools), "tool_calls_as_text": False,
        "thinking_chars": think}
    write_call(os.path.join(run, "qwen3_8b", "simulated", "logs", "01_qualified_full.jsonl"), [
        {"time": "2026-10-15T10:30:00+04:00", "type": "header", "call_id": "abc123def456", "model": "qwen3:8b",
         "mode": "simulated", "scenario": "01_qualified_full", "tests": "Qualified", "lead": "L001 Sarah",
         "prompt_version": "1a2b3c4d5e", "code_version": "68ac601", "settings": {"temperature": 0.2}},
        {"time": "2026-10-15T10:30:01+04:00", "type": "lead", "text": "Hello?"},
        {"time": "2026-10-15T10:30:09+04:00", "type": "agent", "text": "Hi!", "model_seconds": 8.0, "reply_seconds": 8.1,
         "rounds": [rnd(8.0, 6.5, 0.5, 0.9, 900, 20)]},                                   # cold start
        {"time": "2026-10-15T10:30:10+04:00", "type": "lead", "text": "Two million, Marina, apartment, soon."},
        {"time": "2026-10-15T10:30:11+04:00", "type": "tool", "tool": "update_lead", "args": {"budget_aed": 2000000},
         "result": "Saved: budget_aed.", "seconds": 0.01, "ok": True},
        {"time": "2026-10-15T10:30:12+04:00", "type": "tool", "tool": "update_lead", "args": {"property_type": "castle"},
         "result": "Not saved: bad type", "seconds": 0.0, "ok": False},
        {"time": "2026-10-15T10:30:14+04:00", "type": "agent", "text": "Great.", "model_seconds": 4.0, "reply_seconds": 4.1,
         "rounds": [rnd(1.3, 0, 0.3, 0.9, 1200, 30, ["update_lead"]), rnd(1.4, 0, 0.4, 0.9, 1250, 30, ["update_lead"]),
                    rnd(1.3, 0, 0.4, 0.8, 1300, 25)]},                                     # 3 model calls
        {"time": "2026-10-15T10:30:15+04:00", "type": "lead", "text": "Bye"},
        {"time": "2026-10-15T10:30:17+04:00", "type": "agent", "text": "Goodbye!", "model_seconds": 2.0, "reply_seconds": 2.0,
         "rounds": [rnd(2.0, 0, 0.2, 1.7, 1400, 60, [], think=300)], "thinking": ["the lead is done"]},  # thinking
        {"time": "2026-10-15T10:30:18+04:00", "type": "agent", "text": "(no spoken reply)", "model_seconds": 0.6,
         "reply_seconds": 0.6, "rounds": [rnd(0.6, 0, 0.1, 0.4, 1450, 8, ["end_call"])]},  # fast
        {"time": "2026-10-15T10:30:19+04:00", "type": "footer", "duration_seconds": 19.0, "passed": True,
         "ended_by": "end_call", "agent_closed": True, "ending": "Agent closed the call", "result": "qualified",
         "classification": "Qualified", "final_crm_row": "{\"status\": \"qualified\"}", "wrong_fields": []},
    ])
    write_call(os.path.join(folder, "chat", "manual.jsonl"), [
        {"time": "2026-10-15T11:00:00", "type": "header", "model": "qwen3:8b", "lead": "L013 Test"},
        {"time": "2026-10-15T11:00:05", "type": "agent", "text": "Hi", "model_seconds": 1.5},
    ])
