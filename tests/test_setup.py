import json
import os
import tempfile
import unittest

from dashboard import setup_wizard
from dashboard.auth import verify_password
from tests.helpers import make_evproto, make_voice_runs


class SetupWizardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.logs = os.path.join(root, "whatsappai", "ev-proto", "logs")
        make_evproto(self.logs)
        self.test_repo = os.path.join(root, "voice-agent-llm-test")
        os.makedirs(os.path.join(self.test_repo, "llmtest"))
        open(os.path.join(self.test_repo, "run_tests.py"), "w").close()
        make_voice_runs(os.path.join(self.test_repo, "runs"))
        self.dash = os.path.join(root, "ev-agent-dashboard")
        os.makedirs(self.dash)

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_the_agent_folders(self):
        self.assertEqual(setup_wizard.find_whatsapp_logs([self.tmp.name]), [self.logs])
        self.assertEqual(setup_wizard.find_voice_runs([self.tmp.name]), [os.path.join(self.test_repo, "runs")])

    def test_full_run_writes_env_and_admin(self):
        answers = iter(["1", "1", "", "maryam", "Maryam M."])  # 3rd folder (voice agent data): skip
        secrets_typed = iter(["long-enough-pass", "long-enough-pass"])
        setup_wizard._search_roots = lambda here: [self.tmp.name]  # keep the search inside the test folder
        try:
            setup_wizard.run(self.dash, ask=lambda _q: next(answers), ask_secret=lambda _q: next(secrets_typed))
        finally:
            import importlib
            importlib.reload(setup_wizard)
        env = setup_wizard.read_env(os.path.join(self.dash, ".env"))
        self.assertEqual(env["EVPROTO_LOGS_DIR"], self.logs)
        self.assertEqual(env["VOICE_RUNS_DIR"], os.path.join(self.test_repo, "runs"))
        self.assertGreaterEqual(len(env["DASHBOARD_SECRET_KEY"]), 32)
        users = json.load(open(os.path.join(self.dash, "users.json"), encoding="utf-8"))
        self.assertEqual(users["maryam"]["role"], "admin")
        self.assertTrue(verify_password("long-enough-pass", users["maryam"]["password"]))

    def test_rerun_keeps_secret_and_folders(self):
        env_path = os.path.join(self.dash, ".env")
        setup_wizard.write_env(env_path, {"DASHBOARD_SECRET_KEY": "k" * 40, "EVPROTO_LOGS_DIR": self.logs,
                                          "VOICE_RUNS_DIR": "/somewhere"})
        open(os.path.join(self.dash, "users.json"), "w").write('{"a": {"role": "admin", "password": "x"}}')
        answers = iter(["", "", "", "n"])  # keep the folders, no new admin
        setup_wizard._search_roots = lambda here: []
        try:
            setup_wizard.run(self.dash, ask=lambda _q: next(answers), ask_secret=lambda _q: "")
        finally:
            import importlib
            importlib.reload(setup_wizard)
        env = setup_wizard.read_env(env_path)
        self.assertEqual(env["DASHBOARD_SECRET_KEY"], "k" * 40)
        self.assertEqual(env["EVPROTO_LOGS_DIR"], self.logs)
        self.assertEqual(env["VOICE_RUNS_DIR"], "/somewhere")

    def test_offers_logs_of_an_ev_proto_checkout_without_data(self):
        repo = os.path.join(self.tmp.name, "laptop", "me", "ev-proto")
        os.makedirs(os.path.join(repo, "app"))
        for name in ("audit.py", "orchestrator.py"):
            open(os.path.join(repo, "app", name), "w").close()
        found = setup_wizard.find_whatsapp_logs([self.tmp.name])
        self.assertEqual(found[0], self.logs)  # folders with data come first
        self.assertIn(os.path.join(repo, "logs"), found)

    def test_placeholder_from_env_example_is_not_offered(self):
        picked = setup_wizard.choose_folder("x", [self.logs], "/path/to/whatsappai/ev-proto/logs", ask=lambda _q: "")
        self.assertEqual(picked, "")

    def test_short_or_mismatched_password_asks_again(self):
        typed = iter(["short", "long-enough-pass", "different-pass!!", "long-enough-pass", "long-enough-pass"])
        admin = setup_wizard.ask_admin(lambda _q: "", lambda _q: next(typed))
        self.assertEqual(admin["password"], "long-enough-pass")


if __name__ == "__main__":
    unittest.main()
