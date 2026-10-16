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
    write_call(os.path.join(folder, "chat", "manual.jsonl"), [
        {"time": "2026-10-15T11:00:00", "type": "header", "model": "qwen3:8b", "lead": "L013 Test"},
        {"time": "2026-10-15T11:00:05", "type": "agent", "text": "Hi", "model_seconds": 1.5},
    ])
