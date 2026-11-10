"""Maintainer tool: set the shared wind-creator password before a release.

Run from a source checkout: python scripts/set_eeg_release_password.py
Only a salted hash is written into the integration. The password is never printed.
Changing the shipped hash requires distributing an updated integration version.
"""

from getpass import getpass
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.ha_bems.wind_access import PASSWORD_RECORD_FILE, password_record


def main():
    password = getpass("Shared EEG release password (at least 12 characters): ")
    if password != getpass("Confirm password: "):
        raise SystemExit("Passwords do not match; nothing changed")
    try:
        record = password_record(password)
    except ValueError as err:
        raise SystemExit(str(err)) from err
    temporary = PASSWORD_RECORD_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    temporary.replace(PASSWORD_RECORD_FILE)
    print("Updated the shared password hash bundled with HA-BEMS. Include it in the next release.")


if __name__ == "__main__":
    main()
