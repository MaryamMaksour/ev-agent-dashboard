"""Manage dashboard users.

    python manage_users.py add maryam --name "Maryam M."   # asks for the password
    python manage_users.py list
    python manage_users.py remove maryam
    python manage_users.py secret                                       # prints a new DASHBOARD_SECRET_KEY

Every user is an admin and sees everything.
"""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys

from dashboard.auth import ROLE, UserStore, hash_password
from dashboard.settings import Settings, load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_PASSWORD = 12


def main() -> int:
    load_dotenv(os.path.join(HERE, ".env"))
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="add a user or reset their password")
    a.add_argument("username")
    a.add_argument("--name", default="")
    r = sub.add_parser("remove", help="remove a user")
    r.add_argument("username")
    sub.add_parser("list", help="list users")
    sub.add_parser("secret", help="print a new random secret key")
    args = p.parse_args()

    if args.cmd == "secret":
        print(secrets.token_urlsafe(48))
        return 0

    store = UserStore(Settings.from_env().users_file)
    users = store.all()
    if args.cmd == "list":
        if not users:
            print("No users yet.")
        for name, u in sorted(users.items()):
            print(f"{name:<20} {u.get('name', '')}")
        return 0
    username = args.username.strip().lower()
    if args.cmd == "remove":
        if users.pop(username, None) is None:
            print(f"No user called {username}.")
            return 1
        store.save(users)
        print(f"Removed {username}.")
        return 0

    password = getpass.getpass(f"Password for {username}: ")
    if len(password) < MIN_PASSWORD:
        print(f"Use at least {MIN_PASSWORD} characters.")
        return 1
    if getpass.getpass("Repeat it: ") != password:
        print("The two passwords don't match.")
        return 1
    users[username] = {"name": args.name or username, "role": ROLE, "password": hash_password(password)}
    store.save(users)
    print(f"Saved {username}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
