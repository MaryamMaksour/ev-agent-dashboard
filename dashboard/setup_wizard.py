"""First-run setup, so nothing has to be pasted into a terminal.

Run by `python server.py` when .env or users.json is missing (or `python server.py --setup` to redo it):
  1. makes the secret key itself
  2. finds the agents' data folders on this machine and lets you pick by number
  3. asks for the first admin's username and password (typed, never pasted)
  4. writes .env and users.json
"""

from __future__ import annotations

import getpass
import os
import secrets
import sys
from typing import Callable, Optional

from .auth import UserStore, hash_password

MIN_PASSWORD = 12
_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "site-packages", "dist-packages",
              "proc", "sys", "dev", "snap", "Windows", "Program Files", "Program Files (x86)", "AppData"}


# ---------- finding the data folders ----------
def _search_roots(here: str) -> list:
    home = os.path.expanduser("~")
    roots = [os.path.dirname(here), home, "/home", "/opt", "/srv", "C:\\Users", "D:\\"]
    seen, out = set(), []
    for r in roots:
        r = os.path.abspath(r)
        if os.path.isdir(r) and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _walk(root: str, max_depth: int):
    base = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        if dirpath.count(os.sep) - base >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        yield dirpath, dirnames, filenames


def find_whatsapp_logs(roots: list, max_depth: int = 6) -> list:
    """Folders that hold ev-proto's audit.db (normally .../ev-proto/logs)."""
    found = []
    for root in roots:
        for dirpath, _dirs, files in _walk(root, max_depth):
            if "audit.db" in files and (os.path.basename(dirpath) == "logs" or "conversations.db" in files):
                found.append(os.path.abspath(dirpath))
    return _unique(found)


def find_voice_runs(roots: list, max_depth: int = 6) -> list:
    """runs/ folders of voice-agent-llm-test: next to run_tests.py, or holding <run>/<model>/<mode>/logs/*.jsonl."""
    found = []
    for root in roots:
        for dirpath, dirs, files in _walk(root, max_depth):
            if "run_tests.py" in files and "llmtest" in dirs:
                found.append(os.path.abspath(os.path.join(dirpath, "runs")))
            elif os.path.basename(dirpath) == "runs" and any(_looks_like_run(os.path.join(dirpath, d)) for d in dirs):
                found.append(os.path.abspath(dirpath))
    return _unique(found)


def _looks_like_run(path: str) -> bool:
    try:
        for model in os.listdir(path):
            for mode in os.listdir(os.path.join(path, model)):
                if os.path.isdir(os.path.join(path, model, mode, "logs")):
                    return True
    except OSError:
        pass
    return False


def _unique(paths: list) -> list:
    out = []
    for p in paths:
        if p not in out and "ev-agent-dashboard" + os.sep + "sample" not in p:
            out.append(p)
    return out


# ---------- .env ----------
def read_env(path: str) -> dict:
    values = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def write_env(path: str, values: dict) -> None:
    lines = [
        "# Written by python server.py --setup. You can edit it with any text editor.",
        f"EVPROTO_LOGS_DIR={values.get('EVPROTO_LOGS_DIR', '')}",
        f"VOICE_RUNS_DIR={values.get('VOICE_RUNS_DIR', '')}",
        f"DASHBOARD_SECRET_KEY={values['DASHBOARD_SECRET_KEY']}",
        f"DASHBOARD_HOST={values.get('DASHBOARD_HOST', '127.0.0.1')}",
        f"DASHBOARD_PORT={values.get('DASHBOARD_PORT', '8585')}",
        f"DASHBOARD_COOKIE_SECURE={values.get('DASHBOARD_COOKIE_SECURE', '0')}",
        f"DASHBOARD_TRUST_PROXY={values.get('DASHBOARD_TRUST_PROXY', '0')}",
        f"DASHBOARD_USERS_FILE={values.get('DASHBOARD_USERS_FILE', 'users.json')}",
        f"DASHBOARD_ACCESS_LOG={values.get('DASHBOARD_ACCESS_LOG', 'logs/dashboard_access.jsonl')}",
        f"DASHBOARD_SESSION_HOURS={values.get('DASHBOARD_SESSION_HOURS', '8')}",
        f"DASHBOARD_UTC_OFFSET_HOURS={values.get('DASHBOARD_UTC_OFFSET_HOURS', '4')}",
        f"VOICE_LOG_UTC_OFFSET_HOURS={values.get('VOICE_LOG_UTC_OFFSET_HOURS', '4')}",
    ]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------- questions ----------
def choose_folder(label: str, options: list, current: str, ask: Callable[[str], str]) -> str:
    if current and current.startswith("/path/to"):
        current = ""  # the placeholder from .env.example, not a real folder
    print(f"\n{label}")
    if options:
        for i, p in enumerate(options, 1):
            print(f"  {i}. {p}" + ("" if os.path.isdir(p) else "   (no test runs yet)"))
        hint = "Type a number, or type a folder path"
    else:
        print("  Not found automatically.")
        hint = "Type the folder path"
    if current:
        hint += f", or press Enter to keep {current}"
    else:
        hint += ", or press Enter to skip for now"
    while True:
        answer = ask(f"  {hint}: ").strip()
        if not answer:
            return current
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]  # may not exist yet: the voice test creates runs/ on its first run
        if os.path.isdir(answer):
            return os.path.abspath(answer)
        print("  That folder doesn't exist. Try again.")


def ask_admin(ask: Callable[[str], str], ask_secret: Callable[[str], str]) -> Optional[dict]:
    default = getpass.getuser() if hasattr(getpass, "getuser") else "admin"
    username = (ask(f"\nAdmin username [{default}]: ").strip() or default).lower()
    name = ask(f"Display name [{username}]: ").strip() or username
    while True:
        password = ask_secret(f"Password for {username} (at least {MIN_PASSWORD} characters, not shown): ")
        if len(password) < MIN_PASSWORD:
            print(f"  Use at least {MIN_PASSWORD} characters.")
            continue
        if ask_secret("Type it again: ") != password:
            print("  The two passwords don't match. Try again.")
            continue
        return {"username": username, "name": name, "password": password}


def run(here: str, ask: Callable[[str], str] = input, ask_secret: Callable[[str], str] = getpass.getpass) -> bool:
    env_path = os.path.join(here, ".env")
    values = read_env(env_path)
    print("=" * 60)
    print("EV Agent Dashboard setup")
    print("=" * 60)

    if len(values.get("DASHBOARD_SECRET_KEY", "")) < 32:
        values["DASHBOARD_SECRET_KEY"] = secrets.token_urlsafe(48)
        print("Made a new secret key.")

    print("\nLooking for the agents' data on this machine (this can take a few seconds)...")
    roots = _search_roots(here)
    values["EVPROTO_LOGS_DIR"] = choose_folder(
        "WhatsApp agent (ev-proto) logs folder - the one with audit.db:",
        find_whatsapp_logs(roots), values.get("EVPROTO_LOGS_DIR", ""), ask)
    values["VOICE_RUNS_DIR"] = choose_folder(
        "Voice agent test (voice-agent-llm-test) runs folder:",
        find_voice_runs(roots), values.get("VOICE_RUNS_DIR", ""), ask)

    users_file = values.get("DASHBOARD_USERS_FILE", "users.json")
    if not os.path.isabs(users_file):
        users_file = os.path.join(here, users_file)
    store = UserStore(users_file)
    users = store.all()
    if not users or ask("\nAdd another admin? [y/N]: ").strip().lower() == "y":
        admin = ask_admin(ask, ask_secret)
        users[admin["username"]] = {"name": admin["name"], "role": "admin", "password": hash_password(admin["password"])}
        store.save(users)
        print(f"Saved admin {admin['username']}.")

    write_env(env_path, values)
    print(f"\nSaved settings to {env_path}")
    print("Done. Starting the dashboard...\n")
    return True


def interactive() -> bool:
    return sys.stdin is not None and sys.stdin.isatty()
