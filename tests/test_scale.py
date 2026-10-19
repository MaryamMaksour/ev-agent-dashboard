"""The voice call cache: fast at production volume, and never stale for long.

The big test (84,000 calls = 2,800 a day for 30 days) is slow to set up, so it only runs with
RUN_SCALE_TEST=1 (set SCALE_CALLS to change the number)."""

import copy
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from dashboard.voice_calls import VoiceCallSource

UAE = timezone(timedelta(hours=4))
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "voice_agent", "calls",
                       "lead-L001-20261016T093000-2026-10-16T093000.json")


def write_calls(folder: str, n: int, start: datetime) -> None:
    base = json.load(open(FIXTURE, encoding="utf-8"))
    os.makedirs(os.path.join(folder, "calls"), exist_ok=True)
    for i in range(n):
        r = copy.deepcopy(base)
        st = start - timedelta(minutes=i * 30 * 24 * 60 / max(n, 1))
        r["started_at"], r["ended_at"] = st.isoformat(), (st + timedelta(seconds=120)).isoformat()
        r["lead_id"] = f"L{i % 30000:05d}"  # about 30,000 clients, some called more than once
        with open(os.path.join(folder, "calls", f"lead-L{i:06d}-{st:%Y%m%dT%H%M%S}.json"), "w", encoding="utf-8") as f:
            json.dump(r, f)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        write_calls(self.dir, 3, datetime.now(timezone.utc))

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_changed_and_deleted_files_are_picked_up(self):
        src = VoiceCallSource(self.dir, UAE, cache_seconds=0)
        self.assertEqual(src.summary(None)["totals"]["calls"], 3)
        write_calls(os.path.join(self.dir, "more"), 1, datetime.now(timezone.utc))
        extra = os.listdir(os.path.join(self.dir, "more", "calls"))[0]
        os.replace(os.path.join(self.dir, "more", "calls", extra), os.path.join(self.dir, "calls", "lead-LNEW-x.json"))
        self.assertEqual(src.summary(None)["totals"]["calls"], 4)              # new file
        os.remove(os.path.join(self.dir, "calls", "lead-LNEW-x.json"))
        self.assertEqual(src.summary(None)["totals"]["calls"], 3)              # deleted file
        self.assertEqual(len(src._parsed), 3)                                   # and forgotten

    def test_the_list_is_reused_for_a_few_seconds(self):
        src = VoiceCallSource(self.dir, UAE, cache_seconds=60)
        first = src.summary(None)
        os.remove(os.path.join(self.dir, "calls", sorted(os.listdir(os.path.join(self.dir, "calls")))[0]))
        self.assertIs(src.summary(None), first)                                 # within 60 s: same answer
        src._listed = None                                                      # the 60 s are over
        self.assertEqual(src.summary(None)["totals"]["calls"], 2)

    def test_big_folders_refresh_in_the_background(self):
        src = VoiceCallSource(self.dir, UAE, cache_seconds=0)
        src.BACKGROUND_REFRESH_CALLS = 1                                        # as if there were many calls
        self.assertEqual(src.summary(None)["totals"]["calls"], 3)
        os.remove(os.path.join(self.dir, "calls", sorted(os.listdir(os.path.join(self.dir, "calls")))[0]))
        self.assertEqual(src.summary(None)["totals"]["calls"], 3)              # old answer at once...
        for _ in range(100):
            if not src._refreshing:
                break
            time.sleep(0.02)
        self.assertEqual(src.summary(None)["totals"]["calls"], 2)              # ...then the new one

    def test_lists_keep_no_transcript_but_the_detail_view_does(self):
        src = VoiceCallSource(self.dir, UAE, cache_seconds=0)
        c = src.calls()[0]
        self.assertEqual((c["transcript"], c["tool_calls"], c["metrics"]), ([], [], {}))
        self.assertGreater(c["tool_call_count"], 0)
        self.assertTrue(c["turns"])                                             # timings stay for the charts
        full = src.call(c["id"])
        self.assertTrue(full["transcript"] and full["tool_calls"])


@unittest.skipUnless(os.environ.get("RUN_SCALE_TEST") == "1", "slow: set RUN_SCALE_TEST=1")
class ScaleTest(unittest.TestCase):
    def test_production_volume(self):
        n = int(os.environ.get("SCALE_CALLS", "84000"))
        with tempfile.TemporaryDirectory() as d:
            write_calls(d, n, datetime.now(timezone.utc))
            src = VoiceCallSource(d, UAE)
            t0 = time.perf_counter()
            src.summary(30), src.hours(30), src.per_day(30)
            cold = time.perf_counter() - t0
            src._listed = None                                                  # as after the cache time
            t0 = time.perf_counter()
            s = src.summary(30); src.hours(30); src.per_day(30)
            warm = time.perf_counter() - t0
            t0 = time.perf_counter()
            src.summary(30); src.hours(30); src.per_day(30)
            reused = time.perf_counter() - t0
            print(f"\n{n} calls: first load {cold:.1f} s, after the cache time {warm:.2f} s, within it {reused:.3f} s")
            self.assertEqual(s["totals"]["calls"], n)
            self.assertLess(warm, 6.0)
            self.assertLess(reused, 0.1)
