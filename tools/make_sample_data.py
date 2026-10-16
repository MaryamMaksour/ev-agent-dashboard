"""Create SAMPLE data in the same format the agents save, to try the dashboard without the real files.

    python tools/make_sample_data.py            # writes sample/ev-proto-logs and sample/voice-runs

Then in .env:  EVPROTO_LOGS_DIR=sample/ev-proto-logs  and  VOICE_RUNS_DIR=sample/voice-runs
Every name, question and number here is made up.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# same tables as ev-proto app/audit.py and app/store.py
AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, user_id TEXT, name TEXT,
  role TEXT, question TEXT, answer TEXT, sources TEXT, model TEXT, cost_usd REAL, elapsed_ms INTEGER, ip TEXT);
CREATE TABLE IF NOT EXISTS auth_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, identity TEXT,
  success INTEGER, ip TEXT, reason TEXT);
"""
CONV_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT 'New chat', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL,
  role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
"""

PEOPLE = [("sara@example.com", "Sara K.", "user"), ("omar@example.com", "Omar H.", "user"),
          ("lina@example.com", "Lina A.", "user"), ("rami@example.com", "Rami T.", "user"),
          ("maryam@example.com", "Maryam M.", "admin"), ("+971500000099", "Finance desk", "user")]
QUESTIONS = [
    ("How many active deals does LUME RESIDENCES have this month?", "LUME RESIDENCES has 42 active deals created this month.", [{"module": "Deals", "id": "5716838000011"}]),
    ("Top 5 brokers by deal value this year", "| Broker | Value |\n|---|---|\n| Prime Homes | AED 18.2M |", [{"module": "Deals", "id": "5716838000012"}]),
    ("How many leads came in last week?", "312 leads were created last week.", [{"module": "Leads", "id": "5716838000013"}]),
    ("كم عدد الصفقات النشطة في مشروع هايد؟", "يوجد 17 صفقة نشطة في مشروع هايد.", [{"module": "Deals", "id": "5716838000014"}]),
    ("Revenue vs expenses for Q3", "Q3 revenue was AED 41.6M and expenses AED 29.3M.", [{"module": "Books", "id": "pl-q3"}]),
    ("Who is on leave in Sales & Leasing today?", "3 people are on leave today in Sales & Leasing.", [{"module": "MenaMe", "id": "leave"}]),
    ("Deals booked last week by booking date", "I'm not able to pull that up right now. Here are a few things I can help with instead:", []),
    ("Commission owed to Prime Homes", "I'm not able to support that request at the moment. Feel free to rephrase, or ask about Leads, Contacts, Deals, or Accounts.", []),
    ("List every EOI ever", "I couldn't finish that one — it may be too broad to pull in one go. Try narrowing it with a date range, project, or name, or ask for a count first and we'll drill in from there.", []),
    ("What's the weather in Dubai?", "Sorry, that's outside what I can help with. I only answer questions about EV's CRM records (Leads, Contacts, Deals, Accounts).", []),
    ("Hi, I'm new here", "Welcome! Ask me about deals, leads, finance or HR data.", []),
]
VOICE_SCENARIOS = [
    ("01_qualified_full", "Qualified, full info", "L001 Sarah Johnson"),
    ("02_not_interested", "Not interested", "L002 Michael Chen"),
    ("03_callback_tomorrow", "Call me tomorrow at four", "L003 Emily Davis"),
    ("04_do_not_call", "Stop calling me", "L004 James Wilson"),
    ("05_wrong_number", "Wrong number", "L005 Ali Hassan"),
    ("06_no_budget", "Won't give a budget", "L006 Noura Saeed"),
    ("07_budget_range", "Budget range 5-6 million", "L007 David Brown"),
    ("08_callback_next_monday", "Next Monday at ten", "L008 Fatima Zahra"),
]
FIELDS = ["status", "budget_aed", "preferred_area", "property_type", "timeline", "interest_level", "callback_at", "do_not_call"]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def make_whatsapp(folder: str, rnd: random.Random, now: datetime) -> None:
    os.makedirs(folder, exist_ok=True)
    for name in ("audit.db", "conversations.db"):
        p = os.path.join(folder, name)
        if os.path.exists(p):
            os.remove(p)
    audit = sqlite3.connect(os.path.join(folder, "audit.db"))
    audit.executescript(AUDIT_SCHEMA)
    conv = sqlite3.connect(os.path.join(folder, "conversations.db"))
    conv.executescript(CONV_SCHEMA)
    uae = timezone(timedelta(hours=4))
    for day in range(45, -1, -1):
        date = (now - timedelta(days=day)).astimezone(uae)
        weekend = date.weekday() >= 5
        for _ in range(rnd.randint(1, 4) if weekend else rnd.randint(10, 26)):
            uid, name, role = rnd.choice(PEOPLE)
            hour = rnd.choice([9, 9, 10, 10, 10, 11, 11, 12, 14, 15, 15, 16, 17])
            ts = date.replace(hour=hour, minute=rnd.randint(0, 59), second=rnd.randint(0, 59))
            if ts > now:
                continue
            q, a, src = rnd.choice(QUESTIONS)
            model = "claude-haiku-4-5" if rnd.random() < 0.6 else "claude-sonnet-4-6"
            cost = (0.004 if "haiku" in model else 0.018) * rnd.uniform(0.6, 2.2)
            ms = int((1800 if "haiku" in model else 4200) * rnd.uniform(0.6, 3.0))
            audit.execute("INSERT INTO qa_log (ts,user_id,name,role,question,answer,sources,model,cost_usd,elapsed_ms,ip)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (iso(ts), uid, name, role, q, a, json.dumps(src), model, round(cost, 5), ms, "10.0.0." + str(rnd.randint(10, 60))))
            cid = conv.execute("INSERT INTO conversations (user_id,title,created_at,updated_at) VALUES (?,?,?,?)",
                               (uid, q[:40], iso(ts), iso(ts))).lastrowid
            conv.execute("INSERT INTO messages (conversation_id,role,content,created_at) VALUES (?,?,?,?)", (cid, "user", q, iso(ts)))
            conv.execute("INSERT INTO messages (conversation_id,role,content,created_at) VALUES (?,?,?,?)", (cid, "assistant", a, iso(ts)))
            if rnd.random() < 0.08:  # a reworded follow-up or an "are you sure?"
                follow = rnd.choice(["Are you sure?", q + " please"])
                ts2 = ts + timedelta(seconds=rnd.randint(30, 150))
                audit.execute("INSERT INTO qa_log (ts,user_id,name,role,question,answer,sources,model,cost_usd,elapsed_ms,ip)"
                              " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                              (iso(ts2), uid, name, role, follow, a, json.dumps(src), model, round(cost, 5), ms, "10.0.0.20"))
            if rnd.random() < 0.3:
                audit.execute("INSERT INTO auth_log (ts,identity,success,ip,reason) VALUES (?,?,?,?,?)",
                              (iso(ts - timedelta(minutes=2)), uid, 1, "10.0.0.20", "sso"))
        if rnd.random() < 0.25:
            for k in range(rnd.randint(1, 6)):
                audit.execute("INSERT INTO auth_log (ts,identity,success,ip,reason) VALUES (?,?,?,?,?)",
                              (iso(date.replace(hour=3, minute=10 + k)), "+971500000000", 0, "203.0.113.7", "bad_code"))
    audit.commit(); conv.commit(); audit.close(); conv.close()


def make_voice(folder: str, rnd: random.Random) -> None:
    for run, base in (("20261013-151000", datetime(2026, 10, 13, 15, 10)), ("20261015-101500", datetime(2026, 10, 15, 10, 15))):
        for model, skill, speed in (("qwen3:8b", 0.72, 2.1), ("qwen3:14b", 0.9, 3.4), ("qwen3:30b-a3b", 0.95, 2.8)):
            for mode in ("scripted", "simulated"):
                logs = os.path.join(folder, run, model.replace(":", "_"), mode, "logs")
                os.makedirs(logs, exist_ok=True)
                t = base
                for sid, tests, lead in VOICE_SCENARIOS:
                    ev = []

                    def add(e):
                        nonlocal t
                        t += timedelta(seconds=rnd.randint(2, 9))
                        ev.append({"time": t.isoformat(timespec="seconds") + "+04:00", **e})

                    def reply(text, calls=1, cold=False, think=False):
                        rounds, total = [], 0.0
                        for k in range(calls):
                            load = round(rnd.uniform(4, 9), 2) if cold and k == 0 else 0.0
                            read = round(speed * rnd.uniform(0.15, 0.4), 2)
                            write = round(speed * rnd.uniform(0.4, 0.9) * (1.8 if think else 1), 2)
                            secs_ = round(load + read + write + rnd.uniform(0.02, 0.1), 2)
                            total += secs_
                            rounds.append({"seconds": secs_, "load_seconds": load, "prompt_seconds": read,
                                           "generate_seconds": write, "prompt_tokens": rnd.randint(800, 2400),
                                           "output_tokens": rnd.randint(12, 60), "tool_calls": ["update_lead"] if k < calls - 1 else [],
                                           "tool_calls_as_text": False, "thinking_chars": 240 if think else 0})
                        e = {"type": "agent", "text": text, "model_seconds": round(total, 2),
                             "reply_seconds": round(total + 0.01 * calls, 2), "rounds": rounds}
                        if think:
                            e["thinking"] = ["The lead gave a budget; ask about the area next."]
                        add(e)
                    add({"type": "header", "model": model, "mode": mode, "scenario": sid, "tests": tests, "lead": lead, "simulated_now": "2026-10-14T10:00"})
                    add({"type": "lead", "text": "Hello?"})
                    reply("Hi, this is Sara from Bayari Investments. Is now a good time?", cold=(sid == VOICE_SCENARIOS[0][0]))
                    add({"type": "lead", "text": "Yes, go ahead."})
                    if rnd.random() > skill:
                        add({"type": "tool", "tool": "update_lead", "args": {"property_type": "castle"}, "result": "Not saved: property_type must be one of apartment, villa, townhouse, penthouse, plot.", "seconds": 0.0, "ok": False})
                    add({"type": "tool", "tool": "update_lead", "args": {"budget_aed": 2000000}, "result": "Saved: budget_aed.", "seconds": 0.01, "ok": True})
                    add({"type": "note", "text": "rules set: status=interested"})
                    reply("Great. Which area do you prefer?", calls=rnd.choice([1, 2, 2, 3]), think=rnd.random() > skill + 0.04)
                    if rnd.random() > skill + 0.05:
                        add({"type": "note", "text": "guard: removed 'Prices start from AED 1.2 million.'"})
                    add({"type": "tool", "tool": "end_call", "args": {"summary": "Interested."}, "result": "Call ended.", "seconds": 0.0, "ok": True})
                    reply("Thank you, goodbye!")
                    wrong = [] if rnd.random() < skill else rnd.sample(FIELDS, rnd.randint(1, 2))
                    if rnd.random() > skill and wrong:
                        add({"type": "note", "text": "post-call check: filled timeline=6 months"})
                    ended = "end_call" if rnd.random() < skill + 0.08 else "max_turns"
                    status = {"01": "qualified", "02": "not_interested", "03": "callback", "04": "do_not_call",
                              "05": "wrong_number", "06": "interested", "07": "qualified", "08": "callback"}[sid[:2]]
                    if wrong and rnd.random() < 0.5:
                        status = "interested"
                    add({"type": "footer", "duration_seconds": round((t - base).total_seconds() % 90 + 25, 1),
                         "passed": not wrong, "ended_by": ended, "agent_closed": ended == "end_call",
                         "result": status, "classification": {"qualified": "Qualified", "interested": "Interested, details missing",
                         "callback": "Callback booked", "not_interested": "Not interested", "do_not_call": "Do not call",
                         "wrong_number": "Wrong number"}[status],
                         "final_crm_row": json.dumps({"lead_id": lead.split()[0], "status": status, "budget_aed": "2000000"}), "wrong_fields": wrong})
                    with open(os.path.join(logs, sid + ".jsonl"), "w", encoding="utf-8") as f:
                        for e in ev:
                            f.write(json.dumps(e) + "\n")
    chat = os.path.join(folder, "chat")
    os.makedirs(chat, exist_ok=True)
    with open(os.path.join(chat, "qwen3_8b_L013_20261015-1130.jsonl"), "w", encoding="utf-8") as f:
        for e in ({"time": "2026-10-15T11:30:00", "type": "header", "model": "qwen3:8b", "lead": "L013 Test Lead", "now": "2026-10-15T11:30:00"},
                  {"time": "2026-10-15T11:30:04", "type": "lead", "text": "Hello?"},
                  {"time": "2026-10-15T11:30:07", "type": "agent", "text": "Hi, this is Sara. Is now a good time?", "model_seconds": 2.4},
                  {"time": "2026-10-15T11:31:10", "type": "footer", "ended_by": "caller_hung_up", "final_crm_row": "{}"}):
            f.write(json.dumps(e) + "\n")


def main() -> int:
    rnd = random.Random(7)
    now = datetime.now(timezone.utc)
    make_whatsapp(os.path.join(HERE, "sample", "ev-proto-logs"), rnd, now)
    make_voice(os.path.join(HERE, "sample", "voice-runs"), rnd)
    print("Sample data written to sample/ev-proto-logs and sample/voice-runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
