"""Start the dashboard.

    python server.py            # the first time, this also runs the setup questions
    python server.py --setup    # run the setup questions again (change folders, add an admin)

Settings live in .env next to this script (see .env.example). Nothing needs to be pasted.
"""

from __future__ import annotations

import os
import sys

from dashboard import setup_wizard
from dashboard.settings import Settings, load_dotenv
from dashboard.web import serve

HERE = os.path.dirname(os.path.abspath(__file__))


def _needs_setup(settings: Settings) -> bool:
    return len(settings.secret_key) < 32 or not os.path.isfile(settings.users_file)


def main(argv: list) -> int:
    os.chdir(HERE)  # relative paths in .env (users.json, logs/) always mean this folder
    real_env = frozenset(os.environ)
    load_dotenv(os.path.join(HERE, ".env"))
    settings = Settings.from_env()

    if "--setup" in argv or _needs_setup(settings):
        if not setup_wizard.interactive():
            print("Setup is needed but this isn't an interactive terminal.\n"
                  "Run  python server.py --setup  in a terminal once, then start it again.")
            return 1
        setup_wizard.run(HERE)
        load_dotenv(os.path.join(HERE, ".env"), override=True, protect=real_env)
        settings = Settings.from_env()

    for label, path in (("WhatsApp agent logs", settings.evproto_logs_dir), ("Voice test runs", settings.voice_runs_dir)):
        state = "found" if path and os.path.isdir(path) else "NOT FOUND - run: python server.py --setup"
        print(f"{label:<20} {path or '(not set)'}  ({state})")
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
    sys.exit(main(sys.argv[1:]))
