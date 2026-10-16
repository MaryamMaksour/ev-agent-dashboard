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

    def test_viewer_never_gets_text(self):
        page = self.src.question_list(30, show_text=False, now=NOW)
        self.assertTrue(page["items"])
        self.assertTrue(all(i["question"] is None for i in page["items"]))
        qid = page["items"][0]["id"]
        item = self.src.question(qid, show_text=False)
        self.assertIsNone(item["answer"])
        self.assertIsNone(item["ip"])
        self.assertIsNotNone(self.src.question(qid, show_text=True)["answer"])

    def test_filters(self):
        self.assertEqual(self.src.question_list(30, show_text=True, outcome="failed", now=NOW)["total"], 1)
        self.assertEqual(self.src.question_list(30, show_text=True, flag="slow", now=NOW)["total"], 2)
        self.assertEqual(self.src.question_list(30, show_text=True, q="lume", now=NOW)["total"], 2)

    def test_logins_and_conversations(self):
        lg = self.src.login_summary(30, NOW)
        self.assertEqual((lg["total"], lg["failed"]), (3, 2))
        self.assertEqual(lg["top_failed_identities"][0]["name"], "+971500000000")
        cs = self.src.conversation_stats(30, NOW)
        self.assertEqual(cs["conversations"], 2)
        self.assertEqual(cs["avg_questions"], 1.5)
        self.assertEqual(cs["multi_question_share"], 0.5)

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
        self.assertEqual(runs["20261015-101500"]["calls"], 2)
        self.assertEqual(runs["20261015-101500"]["passed"], 1)
        self.assertEqual(runs["chat"]["kind"], "chat")

    def test_run_detail_matches_report_rules(self):
        d = self.src.run_detail("20261015-101500")
        models = {m["model"]: m for m in d["models"]}
        small, big = models["qwen3:8b"], models["qwen3:14b"]
        self.assertEqual(small["verdict"], "NOT ENOUGH")
        self.assertEqual(big["verdict"], "GOOD ENOUGH")
        self.assertEqual(small["field_accuracy"], 0.75)
        self.assertEqual((small["tool_rejected"], small["tool_calls"]), (1, 2))
        self.assertEqual((small["guards"], small["post_call_fills"]), (1, 1))
        self.assertEqual(small["seconds_per_reply"], 3.0)
        self.assertEqual({w["field"] for w in d["wrong_fields"]}, {"status", "budget_aed"})
        self.assertEqual(len(d["matrix"]), 1)
        self.assertEqual(len(d["matrix"][0]["cells"]), 2)

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
