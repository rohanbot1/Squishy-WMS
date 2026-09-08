"""
One-off script: sets (or changes) the shared floor PIN (Wall Builder,
Packer Scan, Shipments).

Prompts for a 4-digit PIN (hidden, typed twice to confirm), hashes it the
same way app/auth.py verifies it, and prints the resulting
FLOOR_PIN_HASH=... line so it can be pasted wherever the app actually
reads its environment from -- a hosted deployment's environment variable
settings (there's no server-local file to write there, and no one to run
this script on the server itself), or a local .env file for local dev,
which this also writes to (creating it if it doesn't exist yet, replacing
the existing line if it does) as a convenience since that file does exist
here.

Usage (run from this repo, any machine -- it only needs to hash a string,
not talk to the actual deployment):
    python scripts/set_floor_pin.py
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
    pin = getpass.getpass("New floor PIN (4 digits): ")
    if not (len(pin) == 4 and pin.isdigit()):
        print("PIN must be exactly 4 digits.")
        sys.exit(1)

    confirm = getpass.getpass("Confirm: ")
    if pin != confirm:
        print("PINs didn't match.")
        sys.exit(1)

    pin_hash = hash_password(pin)
    line = f"FLOOR_PIN_HASH={pin_hash}"

    print()
    print(line)
    print()
    print("Paste the line above into wherever this deployment's environment")
    print("variables are actually set (a hosted platform's dashboard/CLI, or a")
    print("local .env file), then restart the API server for it to take effect.")

    _write_env_var(ENV_PATH, "FLOOR_PIN_HASH", pin_hash)
    print(f"\n(Also wrote it to {os.path.abspath(ENV_PATH)} for local dev convenience.)")


if __name__ == "__main__":
    main()
