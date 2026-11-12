"""Local login: scrypt password hashes in SQLite (standard library only), lockout after repeated failures, roles and scopes."""
import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path

from app.common.access import ROLE_PERMISSIONS, ROLE_SCOPE_LEVEL

MAX_FAILS, LOCK_SECONDS = 5, 900


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or os.urandom(16)
    return salt.hex(), hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    _, h = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(h, hash_hex)


class AuthDB:
    def __init__(self, path: str | None = None):
        self.path = Path(path or os.getenv("AUTH_DB", "data/auth/users.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.execute("""CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, display_name TEXT, role TEXT, scope_level TEXT,
                            scope_value TEXT, permissions TEXT, salt TEXT, pw_hash TEXT, failed INTEGER DEFAULT 0,
                            locked_until REAL DEFAULT 0, active INTEGER DEFAULT 1)""")

    def create_user(self, username, password, role, display_name="", scope_value=None):
        if role not in ROLE_SCOPE_LEVEL:
            raise ValueError(f"unknown role {role}")
        level = ROLE_SCOPE_LEVEL[role]
        if level != "all" and not scope_value:
            raise ValueError(f"role {role} needs a {level} to be scoped to")
        salt, h = hash_password(password)
        self.con.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?,?,0,0,1)",
                         (username, display_name or username, role, level, scope_value, json.dumps(ROLE_PERMISSIONS[role]), salt, h))
        self.con.commit()

    def _user(self, row) -> dict:
        return {"username": row[0], "display_name": row[1], "role": row[2], "scope": {"level": row[3], "value": row[4]},
                "permissions": json.loads(row[5])}

    def get(self, username):
        r = self.con.execute("SELECT username, display_name, role, scope_level, scope_value, permissions FROM users WHERE username = ?", (username,)).fetchone()
        return self._user(r) if r else None

    def list_users(self):
        return [self._user(r) for r in self.con.execute("SELECT username, display_name, role, scope_level, scope_value, permissions FROM users ORDER BY username")]

    def authenticate(self, username: str, password: str, audit=None, now: float | None = None):
        """Return (user or None, reason). Failures and lockouts are written to the audit log."""
        now = now or time.time()
        r = self.con.execute("SELECT username, display_name, role, scope_level, scope_value, permissions, salt, pw_hash, failed, locked_until, active FROM users WHERE username = ?", (username,)).fetchone()
        if r is None or not r[10]:
            hash_password(password)                                   # similar timing for unknown users
            reason = "unknown_or_inactive"
        elif r[9] > now:
            reason = "locked"
        elif verify_password(password, r[6], r[7]):
            self.con.execute("UPDATE users SET failed = 0, locked_until = 0 WHERE username = ?", (username,))
            self.con.commit()
            user = self._user(r[:6])
            if audit:
                audit.append("login_success", user=user)
            return user, "ok"
        else:
            failed = r[8] + 1
            locked = now + LOCK_SECONDS if failed >= MAX_FAILS else 0
            self.con.execute("UPDATE users SET failed = ?, locked_until = ? WHERE username = ?", (0 if locked else failed, locked, username))
            self.con.commit()
            reason = "locked_now" if locked else "bad_password"
        if audit:
            audit.append("login_failure", user={"username": username}, reason=reason)
        return None, reason
