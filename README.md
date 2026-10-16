# EV Agent Dashboard

One place to see what the company's AI agents do: how much they're used, when, by whom, what it costs, how long answers take, and where they go wrong.

**Phase 0** (this version) changes nothing in the agents. It only **reads** what they already save:

| Agent | Repo | What the dashboard reads |
|---|---|---|
| WhatsApp data agent | `ev-proto` (`me` repo) | `logs/audit.db` (every question, answer, cost, time, login) and `logs/conversations.db` |
| Voice agent (tests) | `voice-agent-llm-test` | `runs/<run>/<model>/<mode>/logs/*.jsonl` (every test call) and `runs/chat/*.jsonl` |

The databases are opened **read-only**: the dashboard cannot change the agents' data.

- **Standard library only**: Python 3.9+, no `pip install`, no Docker.
- Runs on the company server next to the agents.

See [docs/phases.md](docs/phases.md) for what each phase adds.

---

## What you see

| Page | Shows |
|---|---|
| **Overview** | Whether each agent's data is found and how fresh it is · WhatsApp questions, cost, answer time · voice test results · questions and cost per day · when the agents are used (weekday × hour, UAE time) · what isn't tracked yet |
| **WhatsApp agent** | Questions, people, Arabic share · how questions ended · typical and slowest answer time · cost per question · top users · models · most cited sources · every question with search and filters · one question in detail |
| **Voice agent tests** | Pick a test run · per model: verdict (same rule as the test report), passed scenarios, field accuracy, seconds per reply, rejected tool calls, how often the code stepped in · scenario × model grid · the fields models get wrong most · one call with the full conversation, tool calls and the final CRM row |
| **Logins** (admins) | Login attempts on the WhatsApp agent, failures per day, most failed identities and IPs |

### Worked out from saved data (until Phase 1 saves the real values)

| Signal | How it's worked out |
|---|---|
| **How a question ended** | ev-proto's fixed replies (`app/i18n.py`) mean *couldn't answer*, *failed to finish* or *off topic*. Saved sources mean *answered with sources*. Anything else is *chat or no sources saved*. |
| **Asked again** | The same person asked a similar question (half the words the same) within 3 minutes |
| **Doubted** | The next message within 3 minutes was "are you sure?", "double check", "متأكد"… |
| **Slow** | Answer took more than 10 seconds |
| **Voice field accuracy** | 8 checked fields minus the wrong ones in each call's footer, as in `llmtest/evaluate.py` |

---

## Who can see what

| Role | Sees |
|---|---|
| `admin` | Everything, including question and answer text, sources, IPs, conversation transcripts and the login log |
| `viewer` | Numbers and charts only. Never the text of a question, an answer or a call |

Logins, failed logins and every time an admin opens a question or a call are written to `logs/dashboard_access.jsonl`.

---

## Set up on the server

```bash
git clone <this repo> ev-agent-dashboard
cd ev-agent-dashboard
cp .env.example .env
python manage_users.py secret              # copy the output into .env as DASHBOARD_SECRET_KEY
nano .env                                  # set EVPROTO_LOGS_DIR and VOICE_RUNS_DIR
python manage_users.py add maryam --role admin --name "Maryam M."
python server.py
```

`server.py` prints whether it found each data folder.

The user that runs the dashboard needs **read** access to both folders. It needs no write access to them.

**Reaching it:** by default it listens on `127.0.0.1` only. From your laptop:

```bash
ssh -L 8585:127.0.0.1:8585 <user>@<server>     # then open http://localhost:8585
```

For everyday use, IT can put it behind HTTPS (nginx or the company's reverse proxy) inside the company network. Then set `DASHBOARD_COOKIE_SECURE=1`. **Do not expose it on the server's public IP**: it shows business, finance and HR answers.

### Run as a service (Linux)

```ini
# /etc/systemd/system/ev-agent-dashboard.service
[Unit]
Description=EV Agent Dashboard
After=network.target

[Service]
User=<service user with read access to the agent logs>
WorkingDirectory=/path/to/ev-agent-dashboard
ExecStart=/usr/bin/python3 server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now ev-agent-dashboard
```

### Users

```bash
python manage_users.py add omar --role viewer --name "Omar H."
python manage_users.py add omar --role viewer      # same command resets a password
python manage_users.py list
python manage_users.py remove omar
```

Passwords are stored as PBKDF2-SHA256 hashes in `users.json`, which is git-ignored. After 5 failed attempts for a username or an IP, sign-in is locked for 15 minutes.

---

## Try it without the real data

```bash
python tools/make_sample_data.py      # writes made-up data to sample/ (git-ignored)
# in .env:
#   EVPROTO_LOGS_DIR=sample/ev-proto-logs
#   VOICE_RUNS_DIR=sample/voice-runs
python server.py
```

## Tests

```bash
python -m unittest discover -s tests -t .
```

They build small data folders in the agents' formats and check:
- the numbers
- the outcome rules
- read-only access
- that viewers never receive text
- login, cookies and lockout
- blocked path tricks and cross-site posts

## Layout

| File | Job |
|---|---|
| `server.py` | Starts the dashboard |
| `manage_users.py` | Add, list and remove users; make a secret key |
| `dashboard/settings.py` | Settings from `.env` / environment |
| `dashboard/whatsapp.py` | Reads ev-proto's `audit.db` and `conversations.db` |
| `dashboard/voice.py` | Reads voice test call logs |
| `dashboard/metrics.py` | Time parsing, percentiles, daily series, heatmap |
| `dashboard/auth.py` | Password hashing, signed session cookie, login lockout |
| `dashboard/web.py` | HTTP server, pages and the read-only JSON API |
| `static/` | The page (HTML, CSS, JS; no external files) |
| `tools/make_sample_data.py` | Made-up data in the agents' formats |
