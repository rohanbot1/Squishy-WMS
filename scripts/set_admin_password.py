"""
One-off script: sets (or changes) the admin login password.

Prompts for a password (hidden, typed twice to confirm), hashes it the same
way app/auth.py verifies it, and writes ADMIN_PASSWORD_HASH into a .env file
in the project root -- creating it if it doesn't exist yet, replacing the
existing line if it does. python-dotenv loads that file automatically when
the API starts, so this is the only step needed to set or change the
password locally.

Usage (run from the project root, same venv as the API server):
    python scripts/set_admin_password.py
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.auth import hash_password

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")


def _write_env_var(env_path: str, key: str, value: str) -> None:
    lines = []
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()

    prefix = f"{key}="
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{prefix}{value}\n"
            replaced = True
            break
    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{prefix}{value}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main() -> None:
    password = getpass.getpass("New admin password: ")
    if not password:
        print("Password cannot be empty.")
        sys.exit(1)

    confirm = getpass.getpass("Confirm: ")
    if password != confirm:
        print("Passwords didn't match.")
        sys.exit(1)

    password_hash = hash_password(password)
    _write_env_var(ENV_PATH, "ADMIN_PASSWORD_HASH", password_hash)
    print(f"Wrote ADMIN_PASSWORD_HASH to {os.path.abspath(ENV_PATH)}")
    print("Restart the API server for it to take effect.")


if __name__ == "__main__":
    main()
