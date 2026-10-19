"""The real voice agent's call records (fixtures written by ev-voice-agent's own CallMetrics/PostCallProcessor)."""

import json
import os
import tempfile
import unittest
from datetime import timedelta, timezone

from dashboard.voice_calls import VoiceCallSource, public_call

UAE = timezone(timedelta(hours=4))
DATA = os.path.join(os.path.dirname(__file__), "fixtures", "voice_agent")
ANSWERED = "lead-L001-20261016T093000-2026-10-16T093000"
BUSY = "lead-L002-20261016T100000-2026-10-16T100000"


class VoiceCallTests(unittest.TestCase):
    def setUp(self):
        self.src = VoiceCallSource(DATA, UAE)

    def test_answered_call(self):
        c = self.src.call(ANSWERED)
        self.assertEqual((c["line_result"], c["classification"], c["ended_by"]), ("answered", "interested", "transferred"))
        self.assertTrue(c["transferred"] and c["recorded"])
        self.assertEqual(c["duration_seconds"], 185.0)
        self.assertEqual((c["ttft_min"], c["ttft_median"], c["ttft_max"]), (0.62, 0.71, 2.4))
        self.assertEqual(c["e2e_max"], 3.06)  # 0.41 + 2.40 + 0.25
        self.assertEqual(c["tokens_in"], 1100 * 3 + 1180)
        self.assertEqual(c["repeat_requests"], 1)
        slow = {t["turn"]: t["slowest_stage"] for t in c["turns"]}
        self.assertEqual(slow[2], "LLM first word")

    def test_busy_and_old_records(self):
        busy = self.src.call(BUSY)
        self.assertEqual((busy["line_label"], busy["classification_label"]), ("Busy", "Not reached"))
        old = self.src.call("lead-L003-old")
        self.assertEqual((old["line_result"], old["classification"]), ("browser", "callback"))  # no line = browser test
        self.assertEqual(old["ttft_median"], 0.8)  # only the summary existed then
        self.assertEqual(old["turns"], [])

    def test_summary(self):
        s = self.src.summary(None)
        t = s["totals"]
        self.assertEqual((t["calls"], t["reached"], t["transferred"], t["recorded"]), (3, 2, 1, 1))
        lines = {x["key"]: x["count"] for x in s["line_results"]}
        self.assertEqual(lines, {"answered": 1, "busy": 1, "browser": 1})
        stages = {x["key"]: x for x in s["stages"]}
        self.assertEqual((stages["llm_ttft"]["min"], stages["llm_ttft"]["max"]), (0.62, 2.4))
        self.assertEqual(s["slowest_stage"], [{"label": "LLM first word", "count": 1}])
        self.assertEqual(s["speech"]["round_trip"]["summary"]["verdict"], "NOT ENOUGH")

    def test_viewer_gets_no_text(self):
        c = public_call(self.src.call(ANSWERED), full=True, show_text=False)
        for k in ("transcript", "tool_calls", "actions", "summary"):
            self.assertIsNone(c[k])
        self.assertNotIn("reason", c["transfer"])
        self.assertTrue(c["turns"])  # timings are fine to show
        run = self.src.speech_run(self.src.speech_runs()[0]["id"], show_text=False)
        self.assertNotIn("transcript", run["items"][0])

    def test_ids_are_checked(self):
        for bad in ("../x", "a/b", "", "..", "x.json/../../etc"):
            self.assertIsNone(self.src.call(bad))
            self.assertIsNone(self.src.speech_run(bad, show_text=True))

    def test_hours(self):
        h = self.src.hours(None)
        self.assertEqual(len(h["hours"]), 24)
        self.assertEqual(sum(r["calls"] for r in h["hours"]), 3)
        self.assertEqual(h["days"], [{"date": "2026-10-16", "calls": 2}, {"date": "2026-10-15", "calls": 1}])
        r13 = h["hours"][13]
        self.assertEqual((r13["calls"], r13["bottleneck"], r13["tokens"]), (1, "llm_ttft", 4576))
        self.assertEqual(r13["stages"]["llm_ttft"], 0.71)
        self.assertEqual(h["slow_reasons"][0]["label"], "LLM first word")
        self.assertEqual(self.src.hours(None, "2026-10-16")["calls"], 2)
        self.assertEqual(self.src.hours(None, "2020-01-01")["calls"], 0)

    def test_calls_at_the_same_time(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "calls"))
            for i, (start, end) in enumerate([("10:00", "10:05"), ("10:02", "10:04"), ("10:03", "10:10"), ("11:00", "11:01")]):
                with open(os.path.join(d, "calls", f"c{i}.json"), "w", encoding="utf-8") as f:
                    json.dump({"lead_id": f"L{i}", "started_at": f"2026-10-16T{start}:00+04:00",
                               "ended_at": f"2026-10-16T{end}:00+04:00"}, f)
            h = VoiceCallSource(d, UAE).hours(None)
            self.assertEqual((h["hours"][10]["calls"], h["hours"][10]["at_once"]), (3, 3))
            self.assertEqual((h["hours"][11]["at_once"], h["max_at_once"]), (1, 3))
            self.assertEqual(h["peak"], {"hour": 10, "calls": 3})

    def test_cost(self):
        from datetime import datetime
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        src = VoiceCallSource(DATA, UAE, costs={"gpu_month": 3000, "other_month": 300, "line_minute": 0.5})
        c = src.summary(None, now=now)["cost"]
        self.assertTrue(c["configured"])
        self.assertEqual(c["period_days"], 5)                    # first call 15 Oct to 19 Oct, UAE days
        self.assertEqual(c["fixed"], 550.0)                      # (3000 + 300) x 5 / 30
        self.assertEqual((c["line_minutes"], c["line"]), (3.1, 1.54))  # answered calls only: 185 s
        self.assertEqual(c["total"], 551.54)
        self.assertEqual((c["per_call"], c["per_reached"], c["per_qualified"]), (183.85, 275.77, None))
        self.assertEqual(len(c["per_day"]), 5)
        self.assertAlmostEqual(sum(d["value"] for d in c["per_day"]), 551.54, places=1)
        week = src.summary(7, now=now)["cost"]
        self.assertEqual((week["period_days"], week["fixed"]), (7, 770.0))
        self.assertFalse(self.src.summary(None, now=now)["cost"]["configured"])   # nothing set

    def test_missing_folder(self):
        src = VoiceCallSource("/nonexistent", UAE)
        self.assertEqual(src.summary(30)["totals"]["calls"], 0)
        self.assertFalse(src.status()["found"])


DATA_V2 = os.path.join(os.path.dirname(__file__), "fixtures", "voice_agent_v2")
V2_CALL = "lead-L004-20261019T100000-2026-10-19T100000"


class VoiceCallV2Tests(unittest.TestCase):
    """Records written by the newer agent: errors list, post-call LLM check, per-call log folder,
    vLLM/Ollama label, and .raw.json files for calls still being processed (fixtures made with the agent's code)."""

    def setUp(self):
        self.src = VoiceCallSource(DATA_V2, UAE)

    def test_unfinished_calls_are_not_counted_as_calls(self):
        ids = [c["id"] for c in self.src.calls()]
        self.assertEqual(ids, [V2_CALL])  # the .raw.json one is left out
        self.assertEqual(self.src.status()["processing"], 1)
        self.assertEqual(self.src.summary(None)["totals"]["processing"], 1)
        self.assertIsNone(self.src.call("lead-L005-20261019T103000-2026-10-19T103000.raw"))

    def test_errors_and_post_call_check(self):
        c = self.src.call(V2_CALL)
        self.assertEqual((c["error_count"], c["error_stages"]), (1, ["tts"]))
        self.assertEqual(c["errors"][0]["stage_label"], "Text-to-speech")
        self.assertEqual(c["served_by"], "vllm")
        self.assertEqual(c["versions"]["code"], "abc1234")
        self.assertEqual(c["interruptions"], 1)
        self.assertTrue(c["post_call_llm"]["ok"])
        self.assertEqual(c["post_call_llm"]["prompt_tokens"] + c["post_call_llm"]["completion_tokens"], 120)
        s = self.src.summary(None)
        self.assertEqual(s["errors_by_stage"], [{"label": "Text-to-speech", "count": 1}])
        self.assertEqual((s["totals"]["calls_with_errors"], s["totals"]["post_call_checks"]), (1, 1))
        self.assertEqual((s["by_model"][0]["model"], s["by_model"][0]["served_by"]), ("qwen3.5-9b", "vllm"))

    def test_llm_requests_and_crm_changes(self):
        full = public_call(self.src.call(V2_CALL), full=True, show_text=True)
        stages = [r["stage"] for r in full["llm_requests"]]
        self.assertEqual(stages, ["call", "call", "post_call"])
        self.assertEqual(full["llm_requests"][1]["tools_called"], ["update_lead"])
        self.assertEqual((full["llm_requests"][0]["prompt_tokens"], full["llm_requests"][0]["cached_tokens"]), (1200, 1100))
        self.assertEqual(full["llm_requests"][2]["prompt_tokens"], 100)
        crm = full["crm_changes"]
        self.assertEqual([x["source"] for x in crm], ["call", "post_call", "post_call"])
        self.assertEqual(crm[0]["changed"]["budget"], {"before": None, "after": 2000000})
        self.assertEqual((full["lead_name"], full["lead_status_before"]), ("Omar Test", "new"))

    def test_viewer_gets_no_text_from_new_fields(self):
        v = public_call(self.src.call(V2_CALL), full=True, show_text=False)
        self.assertNotIn("error", v["errors"][0])               # which stage failed: yes; the message: no
        self.assertEqual(v["errors"][0]["stage"], "tts")
        for r in v["llm_requests"]:
            for k in ("reply", "tool_calls", "error", "messages"):
                self.assertNotIn(k, r)
        for x in v["crm_changes"]:
            self.assertNotIn("changed", x)                       # field names only, no values
        self.assertEqual(v["crm_changes"][0]["fields"], ["budget", "property_type"])
        self.assertIsNone(v["lead_name"])                        # the lead's name: admins only
        self.assertEqual(v["lead_status_before"], "new")
        dumped = json.dumps(v)
        for secret in ("SECRET PROMPT TEXT", "Is this Omar", "Omar Test", "Dubai Hills", "2 million"):
            self.assertNotIn(secret.lower(), dumped.lower())


if __name__ == "__main__":
    unittest.main()
