import tempfile
import unittest
from datetime import timedelta, timezone

from dashboard.whatsapp import WhatsAppSource
from tests.helpers import NOW, make_evproto

UAE = timezone(timedelta(hours=4))


class StepsTests(unittest.TestCase):
    def test_new_columns_and_steps_are_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_evproto(tmp)
            src = WhatsAppSource(tmp, UAE)
            row = next(r for r in src.questions(None, NOW) if r["id"] == 1)
            self.assertEqual(row["language"], "en")
            self.assertEqual(row["details"]["tokens_in"], 1250)
            item = src.question(1, show_text=False)
            self.assertEqual(item["details"]["answer_type"], "crm_data")
            self.assertEqual(item["details"]["finish"], "submit_answer")
            self.assertNotIn("language", item["details"])
            steps = src.steps(1)
            self.assertEqual([s["type"] for s in steps], ["model", "tool"])
            self.assertEqual(src.steps(2), [])

    def test_old_database_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_evproto(tmp, phase2=False)
            src = WhatsAppSource(tmp, UAE)
            rows = src.questions(None, NOW)
            self.assertTrue(rows)
            self.assertEqual({r["language"] for r in rows}, {"en", "ar"})  # still guessed from the text
            self.assertEqual(src.question(1, show_text=True)["details"], {})
            self.assertEqual(src.steps(1), [])
