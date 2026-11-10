"""Shared release password gate; credentials never enter config entries."""

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from time import monotonic

from .const import DOMAIN

PASSWORD_RECORD_FILE = Path(__file__).with_name("wind_access_password.json")
ITERATIONS = 600_000


def password_record(password):
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("Use a password of at least 12 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return {"algorithm": "pbkdf2_sha256", "iterations": ITERATIONS, "salt": salt, "digest": digest}


def verify_password(password):
    try:
        record = json.loads(PASSWORD_RECORD_FILE.read_text(encoding="utf-8"))
        if (record["algorithm"] != "pbkdf2_sha256" or type(record["iterations"]) is not int
                or not ITERATIONS <= record["iterations"] <= 1_000_000
                or len(record["salt"]) != 32 or len(record["digest"]) != 64):
            return "wind_setup_disabled"
        salt, digest = bytes.fromhex(record["salt"]), bytes.fromhex(record["digest"])
    except (OSError, ValueError, KeyError, TypeError):
        return "wind_setup_disabled"
    if not isinstance(password, str) or len(password) > 1024:
        return "invalid_wind_password"
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, record["iterations"])
    return None if hmac.compare_digest(actual, digest) else "invalid_wind_password"


async def async_unlock(hass, password):
    state = hass.data.setdefault(DOMAIN, {}).setdefault("wind_setup_access", {
        "lock": asyncio.Lock(), "failures": 0, "blocked_until": 0.0})
    async with state["lock"]:
        if monotonic() < state["blocked_until"]:
            return "wind_password_locked"
        error = await hass.async_add_executor_job(verify_password, password)
        if error == "invalid_wind_password":
            state["failures"] += 1
            if state["failures"] >= 5:
                state.update(failures=0, blocked_until=monotonic() + 300)
                return "wind_password_locked"
        elif error is None:
            state.update(failures=0, blocked_until=0.0)
        return error
