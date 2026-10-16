"""Start the dashboard.

    python server.py

Settings come from environment variables or a .env file next to this script (see .env.example).
"""

from __future__ import annotations

import os
import sys

from dashboard.settings import Settings, load_dotenv
from dashboard.web import serve

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    load_dotenv(os.path.join(HERE, ".env"))
    settings = Settings.from_env()
    if len(settings.secret_key) < 32:
        print("DASHBOARD_SECRET_KEY is missing or too short.\n"
              "Create one with:  python manage_users.py secret\n"
              "and put it in .env as DASHBOARD_SECRET_KEY=<value>")
        return 1
    if not os.path.isfile(settings.users_file):
        print(f"No users yet ({settings.users_file}). Add the first admin with:\n"
              "  python manage_users.py add <username> --role admin")
        return 1
    for label, path in (("WhatsApp agent logs", settings.evproto_logs_dir), ("Voice test runs", settings.voice_runs_dir)):
        print(f"{label:<20} {path}  {'(found)' if os.path.isdir(path) else '(NOT FOUND - check the path in .env)'}")
    httpd = serve(settings)
    print(f"Dashboard on http://{settings.host}:{settings.port}  (Ctrl+C to stop)")
    if settings.host not in ("127.0.0.1", "localhost"):
        print("Note: listening beyond this machine. Keep it inside the company network and behind HTTPS.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
