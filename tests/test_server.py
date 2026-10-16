import http.client
import json
import os
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from dashboard.auth import LoginLimiter, Sessions, UserStore, hash_password, verify_password
from dashboard.settings import Settings
from dashboard.web import serve
from tests.helpers import make_evproto, make_voice_runs

SECRET = "x" * 40


class AuthUnitTests(unittest.TestCase):
    def test_password_hash(self):
        h = hash_password("correct horse battery")
        self.assertTrue(verify_password("correct horse battery", h))
        self.assertFalse(verify_password("wrong", h))
        self.assertFalse(verify_password("x", "garbage"))

    def test_session_tamper_and_expiry(self):
        s = Sessions(SECRET, 1)
        tok = s.issue({"username": "a", "name": "A", "role": "viewer"}, now=1000)
        self.assertEqual(s.read(tok, now=1001)["role"], "viewer")
        body, sig = tok.rsplit(".", 1)
        self.assertIsNone(s.read(body + "." + sig[:-2] + "aa", now=1001))
        self.assertIsNone(s.read(tok, now=1000 + 3601))
        self.assertIsNone(Sessions("y" * 40, 1).read(tok, now=1001))

    def test_short_secret_rejected(self):
        with self.assertRaises(ValueError):
            Sessions("short", 1)

    def test_limiter(self):
        lim = LoginLimiter(max_fails=3, window=60, lock_for=60)
        for _ in range(3):
            lim.failed("bob", "1.1.1.1", now=100)
        self.assertTrue(lim.locked("bob", "2.2.2.2", now=101))   # same username, other IP
        self.assertTrue(lim.locked("eve", "1.1.1.1", now=101))   # same IP, other username
        self.assertFalse(lim.locked("bob", "1.1.1.1", now=200))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        logs, runs = os.path.join(cls.tmp.name, "logs"), os.path.join(cls.tmp.name, "runs")
        make_evproto(logs)
        make_voice_runs(runs)
        users = os.path.join(cls.tmp.name, "users.json")
        UserStore(users).save({
            "admin": {"name": "Admin", "role": "admin", "password": hash_password("admin-password-1")},
            "view": {"name": "Viewer", "role": "viewer", "password": hash_password("viewer-password-1")},
        })
        cls.access_log = os.path.join(cls.tmp.name, "access.jsonl")
        settings = Settings(evproto_logs_dir=logs, voice_runs_dir=runs, secret_key=SECRET, host="127.0.0.1", port=0,
                            users_file=users, access_log=cls.access_log)
        cls.httpd = serve(settings)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def req(self, method, path, body=None, cookie=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = cookie
        if body is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r, data

    def login(self, user, password):
        r, _ = self.req("POST", "/login", urlencode({"username": user, "password": password}))
        cookie = r.getheader("Set-Cookie")
        return r, cookie.split(";", 1)[0] if cookie else None

    def test_requires_login(self):
        r, _ = self.req("GET", "/")
        self.assertEqual((r.status, r.getheader("Location")), (303, "/login"))
        r, _ = self.req("GET", "/api/me")
        self.assertEqual(r.status, 401)
        r, _ = self.req("GET", "/api/me", cookie="evdash=forged.value")
        self.assertEqual(r.status, 401)

    def test_wrong_password(self):
        r, cookie = self.login("admin", "nope-nope-nope")
        self.assertEqual(r.getheader("Location"), "/login?error=wrong")
        self.assertIsNone(cookie)

    def test_admin_sees_text_viewer_does_not(self):
        _, admin = self.login("admin", "admin-password-1")
        _, viewer = self.login("view", "viewer-password-1")
        r, data = self.req("GET", "/api/whatsapp/questions?range=all", cookie=admin)
        items = json.loads(data)["items"]
        self.assertEqual(r.status, 200)
        self.assertTrue(all(i["question"] for i in items))
        r, data = self.req("GET", "/api/whatsapp/questions?range=all", cookie=viewer)
        self.assertTrue(all(i["question"] is None for i in json.loads(data)["items"]))
        r, data = self.req("GET", f"/api/whatsapp/question?id={items[0]['id']}", cookie=viewer)
        self.assertIsNone(json.loads(data)["answer"])
        r, _ = self.req("GET", "/api/whatsapp/logins?range=all", cookie=viewer)
        self.assertEqual(r.status, 403)
        call = "20261015-101500/qwen3_8b/scripted/logs/01_qualified_full"
        r, data = self.req("GET", f"/api/voice/call?id={call}", cookie=viewer)
        self.assertIsNone(json.loads(data)["events"])
        r, data = self.req("GET", f"/api/voice/call?id={call}", cookie=admin)
        self.assertTrue(json.loads(data)["events"])

    def test_pages_and_api(self):
        _, admin = self.login("admin", "admin-password-1")
        for path in ("/api/me", "/api/status", "/api/overview?range=all", "/api/whatsapp/summary?range=7",
                     "/api/whatsapp/logins?range=all", "/api/voice/runs", "/api/voice/run?id=20261015-101500"):
            r, data = self.req("GET", path, cookie=admin)
            self.assertEqual(r.status, 200, path)
            json.loads(data)
        r, data = self.req("GET", "/", cookie=admin)
        self.assertEqual(r.status, 200)
        self.assertIn(b"EV Agent Dashboard", data)
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        r, _ = self.req("GET", "/static/../dashboard/auth.py")
        self.assertEqual(r.status, 404)
        r, _ = self.req("GET", "/api/voice/call?id=../../etc/passwd", cookie=admin)
        self.assertEqual(r.status, 404)

    def test_cross_site_post_blocked(self):
        r, _ = self.req("POST", "/login", urlencode({"username": "admin", "password": "admin-password-1"}),
                        headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)

    def test_access_log_written(self):
        self.login("admin", "admin-password-1")
        with open(self.access_log, encoding="utf-8") as f:
            text = f.read()
        events = [json.loads(line)["event"] for line in text.splitlines()]
        self.assertIn("login", events)
        self.assertNotIn("admin-password-1", text)


if __name__ == "__main__":
    unittest.main()
