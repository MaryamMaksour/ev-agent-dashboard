"""Voice agent operations: blocked dials, calling-rule compliance, retention (report only), health, alerts."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from dashboard.alerts import OpsSettings, VoiceOps
from dashboard.voice_calls import VoiceCallSource

UAE = timezone(timedelta(hours=4))
NOW = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)  # 12:00 in the UAE


class OpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        os.makedirs(os.path.join(self.dir, "calls"))
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, lead, ago: timedelta, *, line="answered", **extra):
        st = NOW - ago
        rec = {"lead_id": lead, "started_at": st.isoformat(), "ended_at": (st + timedelta(seconds=60)).isoformat(),
               "line": {"result": line, "number": "+97150*****11"} if line else None, **extra}
        self.n += 1
        with open(os.path.join(self.dir, "calls", f"lead-{lead}-{self.n}.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f)

    def health(self, age: timedelta, **services):
        body = {"ts": (NOW - age).isoformat(), "services": {k: {"ok": v, "seconds": 0.1, "error": None if v else
                                                                "[FAIL] cannot connect"} for k, v in services.items()}}
        with open(os.path.join(self.dir, "health.json"), "w", encoding="utf-8") as f:
            json.dump(body, f)

    def blocked(self, *rows):
        os.makedirs(os.path.join(self.dir, "blocked"), exist_ok=True)
        with open(os.path.join(self.dir, "blocked", f"{NOW:%Y-%m-%d}.jsonl"), "w", encoding="utf-8") as f:
            for rule, lead in rows:
                f.write(json.dumps({"ts": NOW.isoformat(), "lead_id": lead, "number": "+97150*****11",
                                    "rule": rule, "detail": "x", "source": "worker"}) + "\n")

    def ops(self, **kw):
        return VoiceOps(VoiceCallSource(self.dir, UAE, cache_seconds=0), OpsSettings(**kw))

    def keys(self, ops):
        return {a["key"]: a["level"] for a in ops.alerts(now=NOW)["alerts"]}

    def test_quiet_healthy_day_has_no_alerts(self):
        self.call("L1", timedelta(hours=1))
        self.health(timedelta(minutes=1), llm=True, stt=True, tts=True, livekit=True, redis=None)
        self.assertEqual(self.keys(self.ops()), {})

    def test_blocked_dials(self):
        self.blocked(("daily_limit", "L1"), ("daily_limit", "L2"), ("llm_down", "L3"))
        b = self.ops().blocked(7, now=NOW)
        self.assertEqual(b["total"], 3)
        self.assertEqual(b["by_rule"][0], {"rule": "daily_limit", "label": "Over the daily limit", "count": 2})
        self.assertEqual(b["recent"][0]["number"], "+97150*****11")
        self.call("L9", timedelta(hours=1))
        self.health(timedelta(minutes=1), llm=True)
        self.assertEqual(self.keys(self.ops()).get("llm_down"), "crit")

    def test_compliance_breaches(self):
        self.call("L1", timedelta(hours=3))
        self.call("L1", timedelta(hours=1))                          # 2 calls the same UAE day: over 1 a day
        self.call("L2", timedelta(days=1, hours=1))
        self.call("L2", timedelta(days=3))
        self.call("L2", timedelta(days=5))                           # 3 in 7 days: over 2 a week
        self.call("L3", timedelta(days=2), classification="not_interested")
        self.call("L3", timedelta(hours=2))                          # called after "not interested"
        self.call("L4", timedelta(hours=2), line=None)               # browser test: not a phone call
        self.call("L4", timedelta(hours=1), line=None)
        c = self.ops().compliance(30, now=NOW)
        self.assertEqual([x["lead_id"] for x in c["over_daily_limit"]], ["L1"])
        self.assertEqual([x["lead_id"] for x in c["over_weekly_limit"]], ["L2"])
        self.assertEqual([x["lead_id"] for x in c["called_after_stop"]], ["L3"])
        self.assertEqual(c["breaches"], 3)
        self.assertEqual(self.ops(max_calls_per_day=0, max_calls_per_week=0).compliance(30, now=NOW)["breaches"], 1)
        self.health(timedelta(minutes=1), llm=True)
        self.assertEqual(self.keys(self.ops()).get("compliance"), "crit")

    def test_retention_is_report_only(self):
        self.call("L1", timedelta(days=400))
        self.call("L2", timedelta(days=10))
        os.makedirs(os.path.join(self.dir, "recordings"))
        old = os.path.join(self.dir, "recordings", "old.ogg")
        with open(old, "wb") as f:
            f.write(b"x" * 10)
        t = (NOW - timedelta(days=400)).timestamp()
        os.utime(old, (t, t))
        before = sorted(os.listdir(os.path.join(self.dir, "calls")))
        ops = self.ops()
        r = ops.retention(now=NOW, wait=True)
        self.assertEqual((r["calls"], r["calls_past_limit"]), (2, 1))
        self.assertEqual((r["sizes"]["recordings"], r["sizes"]["recordings_past_limit"]), (1, 1))
        self.assertIn("nothing is deleted", r["note"])
        self.health(timedelta(minutes=1), llm=True)
        self.assertEqual(self.keys(ops).get("retention"), "warn")
        self.assertEqual(sorted(os.listdir(os.path.join(self.dir, "calls"))), before)   # nothing removed
        self.assertTrue(os.path.exists(old))

    def test_health_alerts(self):
        self.call("L1", timedelta(hours=1))
        self.assertEqual(self.keys(self.ops()).get("health_missing"), "warn")
        self.health(timedelta(minutes=20), llm=True)
        self.assertEqual(self.keys(self.ops()).get("health_stale"), "crit")
        self.health(timedelta(minutes=1), llm=True, stt=False)
        a = {x["key"]: x for x in self.ops().alerts(now=NOW)["alerts"]}
        self.assertEqual(a["down_stt"]["title"], "Speech-to-text is down")
        self.assertIn("cannot connect", a["down_stt"]["detail"])

    def test_call_alerts(self):
        self.health(timedelta(minutes=1), llm=True)
        for i in range(5):
            self.call(f"E{i}", timedelta(hours=1 + i), errors=[{"stage": "tts", "error": "x"}] if i < 2 else [])
        self.call("F1", timedelta(hours=2), ended_by="llm_failure",
                  fallback={"reason": "llm", "callback_at": (NOW - timedelta(hours=1)).isoformat()})
        k = self.keys(self.ops())
        self.assertEqual((k.get("error_rate"), k.get("llm_failure"), k.get("overdue")), ("crit", "crit", "warn"))
        self.assertNotIn("quiet", k)

    def test_no_calls_for_hours(self):
        self.call("L1", timedelta(hours=8))
        self.health(timedelta(minutes=1), llm=True)
        self.assertEqual(self.keys(self.ops()).get("quiet"), "warn")
        self.assertNotIn("quiet", self.keys(self.ops(alert_quiet_hours=0)))

    def test_stuck_processing(self):
        self.call("L1", timedelta(hours=1))
        self.health(timedelta(minutes=1), llm=True)
        raw = os.path.join(self.dir, "calls", "lead-L2-x.raw.json")
        with open(raw, "w") as f:
            f.write("{}")
        self.assertNotIn("stuck", self.keys(self.ops()))
        t = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
        os.utime(raw, (t, t))
        self.assertEqual(self.keys(self.ops()).get("stuck"), "warn")

    def test_no_data_folder(self):
        ops = VoiceOps(VoiceCallSource("/nonexistent", UAE))
        self.assertEqual(ops.alerts(now=NOW), {"alerts": [], "crit": 0, "warn": 0, "found": False})


if __name__ == "__main__":
    unittest.main()
