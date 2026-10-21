import os
import tempfile
import unittest
from datetime import timedelta, timezone

from dashboard import metrics
from dashboard.voice import VoiceTestSource
from dashboard.whatsapp import WhatsAppSource, classify
from tests.helpers import NOW, make_evproto, make_voice_runs

UAE = timezone(timedelta(hours=4))


class WhatsAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        make_evproto(cls.tmp.name)
        cls.src = WhatsAppSource(cls.tmp.name, UAE)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_classify_uses_canned_replies_and_sources(self):
        self.assertEqual(classify("I'm not able to pull that up right now. Here are", []), "no_answer")
        self.assertEqual(classify("لم أتمكن من إكمال هذا الطلب — قد يكون", []), "failed")
        self.assertEqual(classify("Sorry, that's outside what I can help with. I only", []), "off_topic")
        self.assertEqual(classify("42 deals", [{"module": "Deals", "id": "1"}]), "answered")
        self.assertEqual(classify("Hello!", []), "other")

    def test_range_excludes_old_questions(self):
        self.assertEqual(len(self.src.questions(30, NOW)), 7)
        self.assertEqual(len(self.src.questions(None, NOW)), 8)

    def test_summary_totals_and_outcomes(self):
        s = self.src.summary(30, NOW)
        t = s["totals"]
        self.assertEqual(t["questions"], 7)
        self.assertEqual(t["people"], 4)
        self.assertEqual(t["slow"], 2)
        outcomes = {o["key"]: o["count"] for o in s["outcomes"]}
        self.assertEqual(outcomes, {"answered": 2, "no_answer": 1, "failed": 1, "off_topic": 1, "other": 2})
        self.assertAlmostEqual(t["cost_usd"], 0.062, places=4)
        self.assertEqual(len(s["per_day"]), 30)
        self.assertEqual(sum(d["value"] for d in s["per_day"]), 7)
        self.assertEqual(s["top_modules"][0], {"name": "Deals", "count": 2})

    def test_asked_again_and_doubted(self):
        rows = {r["question"]: r for r in self.src.questions(30, NOW)}
        self.assertTrue(rows["How many active deals in LUME?"]["asked_again"])
        self.assertFalse(rows["how many active deals in LUME please"]["asked_again"])
        self.assertTrue(rows["Deals by booking date"]["doubted"])
        self.assertFalse(rows["List every EOI"]["asked_again"])
        self.assertEqual(rows["مرحبا"]["language"], "ar")

    def test_question_text_is_included(self):
        page = self.src.question_list(30, now=NOW)
        self.assertTrue(page["items"])
        self.assertTrue(all(i["question"] for i in page["items"]))
        item = self.src.question(page["items"][0]["id"])
        self.assertIsNotNone(item["answer"])

    def test_filters(self):
        self.assertEqual(self.src.question_list(30, outcome="failed", now=NOW)["total"], 1)
        self.assertEqual(self.src.question_list(30, flag="slow", now=NOW)["total"], 2)
        self.assertEqual(self.src.question_list(30, q="lume", now=NOW)["total"], 2)

    def test_logins_and_conversations(self):
        lg = self.src.login_summary(30, NOW)
        self.assertEqual((lg["total"], lg["failed"]), (3, 2))
        self.assertEqual(lg["top_failed_identities"][0]["name"], "+971500000000")
        cs = self.src.conversation_stats(30, NOW)
        self.assertEqual(cs["conversations"], 2)
        self.assertEqual(cs["avg_questions"], 1.5)
        self.assertEqual(cs["multi_question_share"], 0.5)

    def test_errors_summary_and_list(self):
        s = self.src.error_summary(30, NOW)
        self.assertTrue(s["available"])
        t = s["totals"]
        self.assertEqual(t["errors"], 3)  # the 40-day-old one is outside the range
        self.assertEqual(t["people"], 2)
        self.assertEqual(t["questions"], 2)  # two errors on "List every EOI" count once
        self.assertAlmostEqual(t["question_share"], round(2 / 7, 3))
        self.assertEqual({k["key"] for k in s["by_kind"]}, {"tool_error", "round_limit", "claude_api"})
        self.assertEqual(s["by_system"], [{"name": "Zoho CRM", "count": 1}])
        self.assertEqual(sum(d["value"] for d in s["per_day"]), 3)
        self.assertEqual(self.src.error_summary(None, NOW)["totals"]["errors"], 4)
        page = self.src.error_list(30, kind="claude_api", now=NOW)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["kind_label"], "Claude API error")

    def test_error_text_is_included(self):
        page = self.src.error_list(None, now=NOW)
        self.assertTrue(page["items"])
        self.assertTrue(all(i["message"] for i in page["items"]))
        self.assertIsNotNone(self.src.error(page["items"][0]["id"])["message"])

    def test_old_agent_without_error_log(self):
        import shutil
        import sqlite3
        folder = os.path.join(self.tmp.name, "old")
        os.makedirs(folder)
        shutil.copy(self.src.audit_path, folder)
        conn = sqlite3.connect(os.path.join(folder, "audit.db"))
        conn.execute("DROP TABLE error_log")
        conn.commit()
        conn.close()
        s = WhatsAppSource(folder, UAE).error_summary(30, NOW)
        self.assertFalse(s["available"])
        self.assertEqual(s["totals"]["errors"], 0)

    def test_alerts(self):
        from datetime import timedelta
        from dashboard.whatsapp import AlertSettings
        keys = lambda src, now=NOW: {a["key"]: a["level"] for a in src.alerts(now)["alerts"]}
        # 7 questions in the last day: below the 10 needed for the answer checks; 2 of 7 hit an error (> 10 %)
        self.assertEqual(keys(self.src), {"wa_error_rate": "crit"})
        strict = WhatsAppSource(self.tmp.name, UAE, AlertSettings(failed_logins=2))
        self.assertEqual(keys(strict)["wa_logins"], "crit")
        # Monday, 3 days after the last question: nobody asked on a workday
        later = keys(self.src, NOW + timedelta(days=3))
        self.assertEqual(later, {"wa_quiet": "warn"})
        # Saturday: the weekend is quiet on purpose
        self.assertNotIn("wa_quiet", keys(self.src, NOW + timedelta(days=1, hours=2)))
        missing = WhatsAppSource(os.path.join(self.tmp.name, "nope"), UAE)
        self.assertEqual(missing.alerts(NOW), {"alerts": [], "crit": 0, "warn": 0, "found": False})

    def test_missing_folder_is_not_an_error(self):
        src = WhatsAppSource(os.path.join(self.tmp.name, "nope"), UAE)
        self.assertEqual(src.summary(30, NOW)["totals"]["questions"], 0)
        self.assertFalse(src.status()["audit_db"])

    def test_database_is_opened_read_only(self):
        import sqlite3
        from contextlib import closing
        from dashboard.whatsapp import _connect
        with closing(_connect(self.src.audit_path)) as conn, self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM qa_log")


class VoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        make_voice_runs(cls.tmp.name)
        cls.src = VoiceTestSource(cls.tmp.name, UAE, UAE)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_runs(self):
        runs = {r["id"]: r for r in self.src.runs()}
        self.assertEqual(set(runs), {"20261015-101500", "chat"})
        self.assertEqual(runs["20261015-101500"]["calls"], 3)
        self.assertEqual(runs["20261015-101500"]["passed"], 2)
        self.assertEqual(runs["chat"]["kind"], "chat")

    def test_run_detail_matches_report_rules(self):
        d = self.src.run_detail("20261015-101500")
        models = {m["model"]: m for m in d["models"]}
        small, big = models["qwen3:8b"], models["qwen3:14b"]
        self.assertEqual(small["verdict"], "NOT ENOUGH")
        self.assertEqual(big["verdict"], "GOOD ENOUGH")
        self.assertEqual(small["field_accuracy"], 0.875)  # (0.75 + 1.0) / 2 calls
        self.assertEqual((small["tool_rejected"], small["tool_calls"]), (2, 4))
        self.assertEqual((small["guards"], small["post_call_fills"]), (1, 1))
        self.assertEqual({w["field"] for w in d["wrong_fields"]}, {"status", "budget_aed"})
        self.assertEqual(len(d["matrix"]), 1)
        self.assertEqual(len(d["matrix"][0]["cells"]), 3)

    def test_new_log_tracking_fields(self):
        c = self.src.call("20261015-101500/qwen3_8b/simulated/logs/01_qualified_full")
        self.assertTrue(c["agent_closed"])
        self.assertEqual((c["result"], c["classification"]), ("qualified", "Qualified"))
        self.assertEqual(c["duration_seconds"], 19.0)
        self.assertEqual((c["wait_min"], c["wait_median"], c["wait_max"]), (0.6, 3.05, 8.1))
        reasons = [w["reason"] for w in c["waits"]]
        self.assertEqual(reasons, ["model_loading", "many_model_calls", "thinking", "long_reply"])
        self.assertEqual(c["slowest_reason"], "Model was loading (cold start)")
        self.assertEqual(c["model_calls"], 6)
        self.assertEqual(c["thinking_replies"], 1)
        self.assertEqual(c["tool_rejected"], 1)
        self.assertEqual(c["prompt_version"], "1a2b3c4d5e")

    def test_old_logs_still_work(self):
        c = self.src.call("20261015-101500/qwen3_8b/scripted/logs/01_qualified_full")
        self.assertEqual(c["waits"][0]["reason"], "not_recorded")
        self.assertEqual(c["classification"], "Interested, details missing")  # derived from the final CRM row
        self.assertTrue(c["agent_closed"])
        self.assertEqual(c["duration_seconds"], 9.0)  # from the first and last event times

    def test_run_level_classification_and_slow_reasons(self):
        d = self.src.run_detail("20261015-101500")
        classes = {x["label"]: x["total"] for x in d["classification"]}
        self.assertEqual(classes["Qualified"], 1)
        slow = {x["label"]: x["count"] for x in d["slow_reasons"]}
        self.assertEqual(slow["Model was loading (cold start)"], 1)
        self.assertEqual(slow["Several model calls (tool round trips)"], 1)
        small = {m["model"]: m for m in d["models"]}["qwen3:8b"]
        self.assertEqual(small["wait_max"], 8.1)
        self.assertEqual(small["slowest"]["reason"], "Model was loading (cold start)")
        self.assertEqual(small["agent_closed"], 2)

    def test_call_lookup_blocks_path_tricks(self):
        good = "20261015-101500/qwen3_8b/scripted/logs/01_qualified_full"
        self.assertIsNotNone(self.src.call(good))
        for bad in ("../etc/passwd", "/etc/passwd", good + "/../../../../x", "a\\b", ""):
            self.assertIsNone(self.src.call(bad), bad)

    def test_incomplete_log(self):
        c = self.src.call("chat/manual")
        self.assertEqual(c["ended_by"], "incomplete")
        self.assertIsNone(c["passed"])


class MetricsTests(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual(metrics.percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertIsNone(metrics.percentile([], 0.5))

    def test_heatmap_uses_local_time(self):
        items = [{"ts": NOW}]  # 08:00 UTC Friday = 12:00 UAE
        hm = metrics.heatmap(items, UAE)
        self.assertEqual(hm["peak"], {"weekday": "Fri", "hour": 12, "count": 1})

    def test_naive_times_use_given_zone(self):
        self.assertEqual(metrics.parse_ts("2026-10-15T10:00:00", UAE).utcoffset(), timedelta(hours=4))
        self.assertIsNone(metrics.parse_ts("not a date"))


if __name__ == "__main__":
    unittest.main()
