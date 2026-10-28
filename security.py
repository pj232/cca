# -*- coding: utf-8 -*-
"""Mots de passe (scrypt), authentification et blocage anti-force-brute persistant."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time

from . import config, db


class LockedError(Exception):
    def __init__(self, wait_seconds: int):
        super().__init__(f"Compte temporairement bloqué ({wait_seconds} s).")
        self.wait_seconds = wait_seconds


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                             n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(key.hex(), key_hex)
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    """Retourne un message d'erreur si le mot de passe est trop faible, sinon None."""
    if len(password) < config.MIN_PASSWORD:
        return f"Au moins {config.MIN_PASSWORD} caractères."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Le mot de passe doit contenir au moins une lettre et un chiffre."
    return None


def _recent_failures(username: str) -> list[float]:
    since = time.time() - config.BLOCAGE_SECONDES
    return db.col("SELECT ts FROM login_attempts WHERE username=? AND ts>=? ORDER BY ts", (username, since))


def authenticate(username: str, password: str) -> dict | None:
    """Retourne l'utilisateur, None si identifiants invalides, ou lève LockedError."""
    username = username.strip()
    fails = _recent_failures(username)
    if len(fails) >= config.MAX_ECHECS:
        wait = int(config.BLOCAGE_SECONDES - (time.time() - fails[0])) + 1
        raise LockedError(max(wait, 1))
    row = db.one("SELECT * FROM users WHERE username=? AND active=1", (username,))
    ok = bool(row) and verify_password(password, row["password_hash"])
    if not row:
        hash_password(password)  # temps de réponse comparable
    if ok:
        db.execute("DELETE FROM login_attempts WHERE username=?", (username,))
        return {k: row[k] for k in ("id", "username", "full_name", "role", "active", "must_change")}
    db.execute("INSERT INTO login_attempts(username, ts) VALUES (?, ?)", (username, time.time()))
    return None
