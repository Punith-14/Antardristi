"""
Manage user accounts from the command line - for the first admin, and for
when nobody can sign in.

Run from backend/:

    python -m scripts.manage_users create punith --role admin
    python -m scripts.manage_users list
    python -m scripts.manage_users role asha analyst
    python -m scripts.manage_users password asha
    python -m scripts.manage_users disable asha
    python -m scripts.manage_users enable asha

Passwords are typed at a hidden prompt, never passed on the command line
(where they would land in the shell history).
"""

import argparse
import getpass
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from core import auth  # noqa: E402


def ask_password(prompt="Password: "):
    first = getpass.getpass(prompt)
    if getpass.getpass("Again: ") != first:
        sys.exit("The passwords did not match.")
    return first


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manage Antardrishti user accounts.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("username")
    create.add_argument("--role", choices=auth.ROLES, default="viewer")
    sub.add_parser("list")
    role = sub.add_parser("role")
    role.add_argument("username")
    role.add_argument("role", choices=auth.ROLES)
    for name in ("password", "disable", "enable", "delete"):
        sub.add_parser(name).add_argument("username")
    args = parser.parse_args(argv)

    try:
        if args.command == "create":
            user = auth.create_user(args.username, ask_password(), args.role)
            print(f"created {user['username']} ({user['role']})")
        elif args.command == "list":
            for u in auth.list_users():
                state = "" if u["active"] else "  [disabled]"
                print(f"{u['username']:<24}{u['role']:<9}last login {u['last_login'] or 'never'}{state}")
        elif args.command == "role":
            print(auth.update_user(args.username, role=args.role))
        elif args.command == "password":
            auth.update_user(args.username, password=ask_password("New password: "))
            print("password changed")
        elif args.command in ("disable", "enable"):
            auth.update_user(args.username, active=args.command == "enable")
            print(f"{args.username} {args.command}d")
        elif args.command == "delete":
            auth.delete_user(args.username)
            print(f"{args.username} deleted")
    except auth.AuthError as exc:
        sys.exit(exc.message)


if __name__ == "__main__":
    main()
