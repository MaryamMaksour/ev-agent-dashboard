# Phases

| Phase | Goal | Changes to the agents | Dashboard gets |
|---|---|---|---|
| **0 (done)** | Show what is already saved | None | Usage, cost, time, busiest hours, every question, voice test results, logins |
| **1** | One complete record per question / call | Save values the agents already compute but drop | Real answer type, confidence, tokens, department, versions |
| **2** | The steps inside | Save each model call and tool call | Step-by-step timeline: what it planned, which tools ran, what came back |
| **3** | Error types | Tag each failure with a category | Most common errors, trend per day, before/after each release |
| **4** | Learning loop | Feedback buttons, review queue | Reviews, test set growing from real mistakes, move to AWS with the voice agent |

✅ saved and shown in Phase 0 · ⚠️ computed in the agent but not saved · ❌ not there yet

## WhatsApp agent (ev-proto)

### Phase 1: one record per question

| Field | Today | Where |
|---|---|---|
| Time, user id, name, role, question, answer, sources, model, cost, time, IP | ✅ | `qa_log` |
| Answer type (`crm_data` / `no_data` / `chitchat` / `off_topic`) | ⚠️ returned by the model, dropped in `_answer_question` | add to `_result()` in `orchestrator.py` |
| Confident | ⚠️ in the result, not logged | `audit.record_qa` |
| Language | ⚠️ in the result, not logged | `audit.record_qa` |
| Conversation id + position in it | ⚠️ known in `/chat`, not logged | `main.py` → `audit.record_qa` |
| Tokens in / out / cache read / cache write, number of model calls | ⚠️ `usage` dict | `answer_question` |
| Model requested vs model used, router reason | ⚠️ partly | `model_router.select_model` |
| Department / job title | ❌ | look up in MenaMe by email |
| Login method, channel | ⚠️ in the session | `main.py` |
| App version (git sha), test vs real | ❌ | config |

### Phase 2: the steps inside

| Step | What to save |
|---|---|
| Each model call | model, seconds, tokens, stop reason, the text it wrote (its plan), tools requested |
| Each tool call (17 tools) | tool, system (Zoho CRM / Books / MenaMe), the query, seconds, rows, `more_records`, error |
| Safety checks | grounding result, **numbers removed by `_has_ungrounded_figure`**, forced re-answers |
| How it finished | which of the return paths in `_answer_question`, number of rounds, hit the 10-round limit |

### Phase 3: error types

1. Wrong field or filter (Zoho rejects the query)
2. Connection (Zoho token, Oracle timeout)
3. Claude API errors (429 / 529 / timeout)
4. Answer cut off (`max_tokens`)
5. Round limit reached
6. Ungrounded number removed
7. No data
8. Person unhappy (asked again, "are you sure?", 👎)
9. Slow

**Note:** Claude API errors are not caught today. They reach the user as a 500 error.

## Voice agent (voice-agent-llm-test)

### Phase 1: one record per call

**Done** in voice-agent-llm-test `68ac601`: call id, versions, settings, duration, agent closed the call, result, classification, tokens and Ollama timings, local times with UTC offset.

| Field | Today |
|---|---|
| Run, model, mode, scenario, lead, start time, how it ended, replies, model seconds | ✅ |
| Passed, wrong fields, final CRM row, tool calls, guard and post-call notes | ✅ |
| Unique call id, repeat number, tester name (`chat.py`) | ❌ |
| Prompt / code version, settings (temperature, context, max turns, post-call on/off) | ❌ |
| Tokens in / out | ⚠️ in `ChatResult`, not logged |
| Model speed (tokens/s) and model load time | ❌ Ollama returns `eval_duration`, `load_duration` |
| Times in UTC | ❌ written in local time without a zone |
| Phone line, recording, speech costs, consent, do-not-call and calling-hours checks | later, with LiveKit and 3CX |

### Phase 2: the steps inside

**Done** for the text test: each model call's timings and tokens, tool seconds and ok, tool calls written as text, thinking text.

| Step | What to save |
|---|---|
| Each model call in a reply (up to 6) | seconds, tokens, whether the tool call came as a real call or as text |
| Tool result | ok / rejected as its own field, not only text |
| `<think>` text removed by `clean_text` | whether it happened and how long it was (it slows replies) |
| Later, with audio | speech-to-text confidence, end-of-speech wait, time to first audio, interruptions |

### Phase 3: error types

1. Wrong or missing field (which field)
2. Tool call rejected
3. Unknown tool, or tool call written as text
4. Ended too early, or never ended
5. Stuck in tool rounds
6. Code stepped in: price guard, tool-talk removal, post-call fills, rules
7. Style: replies over 40 words, more than one question
8. Ollama unreachable or timed out
9. Slow replies

With audio:
- Phone line
- Speech recognition
- Interruptions
- Compliance
