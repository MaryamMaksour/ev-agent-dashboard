"""The real voice agent's call records (fixtures written by ev-voice-agent's own CallMetrics/PostCallProcessor)."""

import os
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

    def test_missing_folder(self):
        src = VoiceCallSource("/nonexistent", UAE)
        self.assertEqual(src.summary(30)["totals"]["calls"], 0)
        self.assertFalse(src.status()["found"])


if __name__ == "__main__":
    unittest.main()
