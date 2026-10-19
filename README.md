# EV Agent Dashboard

One place to see what the company's AI agents do: how much they're used, when, by whom, what it costs, how long answers take, and where they go wrong.

**Phase 0** (this version) changes nothing in the agents. It only **reads** what they already save:

| Agent | Repo | What the dashboard reads |
|---|---|---|
| WhatsApp data agent | `ev-proto` (`me` repo) | `logs/audit.db` (every question, answer, cost, time, login) and `logs/conversations.db` |
| Voice agent (tests) | `voice-agent-llm-test` | `runs/<run>/<model>/<mode>/logs/*.jsonl` (every test call) and `runs/chat/*.jsonl` |
| Voice agent (real calls) | `ev-voice-agent` | `data/calls/*.json` (every LiveKit call) and `data/speech_evals/*.json` (speech accuracy runs) |

The databases are opened **read-only**: the dashboard cannot change the agents' data.

- **Standard library only**: Python 3.9+, no `pip install`, no Docker.
- Runs on the company server next to the agents.

See [docs/phases.md](docs/phases.md) for what each phase adds.

---

## What you see

| Page | Shows |
|---|---|
| **Overview** | Whether each agent's data is found and how fresh it is · WhatsApp questions, cost, answer time · number of voice agent calls · questions and cost per day · when the WhatsApp agent is used (weekday × hour, UAE time) · what isn't tracked yet |
| **WhatsApp agent** | Questions, people, Arabic share · how questions ended · typical and slowest answer time · cost per question · top users · models · most cited sources · every question with search and filters · one question in detail |
| **Voice agent calls** | Calls, how many reached the lead, whether the agent closed the call, typical call length, **tokens per call**, asked for a person / transferred, recorded · **waiting time per stage: shortest / typical / slowest 5% / longest** for the whole turn, speech-to-text, **LLM first token** and text-to-speech · **phone line results** (answered / busy / no answer / declined / invalid number) · classification · how calls ended · what slows slow turns · **speech accuracy** (latest round-trip and real-recording runs) · per model, and whether it ran on **vLLM** (production) or **Ollama** (testing) · **errors by stage** (speech-to-text, LLM, text-to-speech, CRM, post-call check) · the **post-call check** (time and tokens) · calls **still being processed** · every call with each turn's timings and tokens, **every LLM request**, **every CRM change (before → after)**, errors, tool calls, transcript, recording, transfer, and the code and prompt version |
| **Logins** (admins) | Login attempts on the WhatsApp agent, failures per day, most failed identities and IPs |

### Worked out from saved data (until Phase 1 saves the real values)

| Signal | How it's worked out |
|---|---|
| **How a question ended** | ev-proto's fixed replies (`app/i18n.py`) mean *couldn't answer*, *failed to finish* or *off topic*. Saved sources mean *answered with sources*. Anything else is *chat or no sources saved*. |
| **Asked again** | The same person asked a similar question (half the words the same) within 3 minutes |
| **Doubted** | The next message within 3 minutes was "are you sure?", "double check", "متأكد"… |
| **Slow** | Answer took more than 10 seconds |
| **Voice field accuracy** | 8 checked fields minus the wrong ones in each call's footer, as in `llmtest/evaluate.py` |
| **Why a reply was slow** | From that reply's model-call timings: model loading ≥ 40% of the wait → *cold start*; 3+ model calls → *tool round trips*; thinking and writing ≥ 40% → *hidden thinking*; otherwise the biggest part: *reading the conversation*, *writing the reply*, *running tools* or *server overhead*. "Slow" means 1.5 s or more. Logs from before the voice test recorded timings show *not recorded*. |
| **Call classification** | The lead's status after the call: Qualified · Interested, details missing · Callback booked · Not interested · Do not call · Wrong number · No result saved |

---

## Who can see what

| Role | Sees |
|---|---|
| `admin` | Everything, including question and answer text, sources, IPs, conversation transcripts and the login log |
| `viewer` | Numbers and charts only. Never the text of a question, an answer or a call |

Logins, failed logins and every time an admin opens a question or a call are written to `logs/dashboard_access.jsonl`.

---

## Set up on the server (nothing to paste)

**1. Put the code on the server**, either way:
- **Without typing anything:** on your laptop open the repo on GitHub → **Code** → **Download ZIP**. Copy the ZIP to the server (for example with ManageEngine File Transfer) and unzip it.
- Or type `git clone https://github.com/MaryamMaksour/ev-agent-dashboard` on the server.

**2. Start it.** In the folder, type:

```bash
python server.py
```

The first time, it asks a few questions and sets everything up itself:

```
Made a new secret key.
Looking for the agents' data on this machine...

WhatsApp agent (ev-proto) logs folder - the one with audit.db:
  1. /home/shameer/whatsappai/ev-proto/logs
  Type a number, or type a folder path, or press Enter to skip for now: 1

Voice agent test (voice-agent-llm-test) runs folder:
  1. /home/.../voice-agent-llm-test/runs
  Type a number, ...: 1

Admin username [shameer]: maryam
Display name [maryam]: Maryam M.
Password for maryam (at least 12 characters, not shown): ************
Type it again: ************
```

It writes `.env` and `users.json` (both readable only by you), then starts the dashboard. It also prints whether each data folder was found.

- To change the folders or add another admin later: `python server.py --setup`
- On Windows, type `py server.py` if `python` isn't found.

The user that runs the dashboard needs **read** access to both folders. It needs no write access to them.

**Reaching it:** by default it listens on `127.0.0.1` only. From your laptop:

```bash
ssh -L 8585:127.0.0.1:8585 <user>@<server>     # then open http://localhost:8585
```

For everyday use, IT can put it behind HTTPS (nginx or the company's reverse proxy) inside the company network. Then set `DASHBOARD_COOKIE_SECURE=1`. **Do not expose it on the server's public IP**: it shows business, finance and HR answers.

## Run it on your laptop

Until the VPN is ready, the dashboard can run on a laptop. It listens on `127.0.0.1` only, so nothing is opened to the network.

**1. Put the three repos in one folder** (the setup looks next to the dashboard first):

```
projects/
├── me/                     ← ev-proto is in me/ev-proto
├── voice-agent-llm-test/
└── ev-agent-dashboard/
```

**2. Start it:** `python server.py` (Windows: `py server.py`, Mac: `python3 server.py`). The setup offers `me/ev-proto/logs` and `voice-agent-llm-test/runs` by number. Then open http://localhost:8585.

**Where the data comes from on a laptop.** The agents' data files are not in git (they hold real questions and answers), so a fresh clone starts empty. The dashboard fills in from either source:

| Source | How | Note |
|---|---|---|
| **Run the agents on the laptop** | Ask ev-proto questions locally, or run `python run_tests.py` | ev-proto writes `logs/audit.db` on its first answer. MenaMe HR (Oracle) is only reachable inside the company network. The voice test needs Ollama on the laptop (`OLLAMA_URL`). |
| **Copy a snapshot from the server** | ManageEngine File Transfer: copy `audit.db` and `conversations.db` into `me/ev-proto/logs/`, and the `runs/` folder into `voice-agent-llm-test/` | These files hold real business, finance and HR answers. Only copy them to an encrypted laptop, and delete them when you're done. |

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
python server.py --setup              # at the folder questions, type: sample/ev-proto-logs  and  sample/voice-runs
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
| `server.py` | Starts the dashboard (runs the setup questions the first time) |
| `dashboard/setup_wizard.py` | First-run setup: secret key, finds the data folders, first admin |
| `manage_users.py` | Add, list and remove users; make a secret key |
| `dashboard/settings.py` | Settings from `.env` / environment |
| `dashboard/whatsapp.py` | Reads ev-proto's `audit.db` and `conversations.db` |
| `dashboard/voice.py` | Reads voice test call logs |
| `dashboard/metrics.py` | Time parsing, percentiles, daily series, heatmap |
| `dashboard/auth.py` | Password hashing, signed session cookie, login lockout |
| `dashboard/web.py` | HTTP server, pages and the read-only JSON API |
| `static/` | The page (HTML, CSS, JS; no external files) |
| `tools/make_sample_data.py` | Made-up data in the agents' formats |
