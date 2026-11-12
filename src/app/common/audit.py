"""Tamper-evident audit trail: append-only JSON lines, each record keyed-hashed (HMAC-SHA256) together with the hash of the
previous record. Editing, deleting or re-ordering a record breaks the chain, and without the key a chain cannot be recomputed.
Limit: cutting off the END of the file is only detectable if you keep the head hash somewhere else (see `head`)."""
import fcntl
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _canon(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class AuditLog:
    def __init__(self, path: str, key: bytes):
        self.path, self.key = Path(path), key
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def _mac(self, prev: str, rec: dict) -> str:
        return hmac.new(self.key, (prev + _canon(rec)).encode(), hashlib.sha256).hexdigest()

    def _records(self) -> list[dict]:
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def append(self, event: str, user: dict | None = None, **details) -> dict:
        with open(self.path, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            lines = [l for l in f.read().splitlines() if l.strip()]
            prev = json.loads(lines[-1]) if lines else {"seq": 0, "hash": GENESIS}
            u = {k: user[k] for k in ("username", "role", "scope") if user and k in user}
            rec = {"v": 1, "seq": prev["seq"] + 1, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                   "event": event, "user": u, "details": details, "prev": prev["hash"]}
            rec["hash"] = self._mac(rec["prev"], {k: v for k, v in rec.items() if k not in ("hash", "prev")})
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
            fcntl.flock(f, fcntl.LOCK_UN)
        return rec

    def verify(self) -> dict:
        prev_hash, prev_seq, n = GENESIS, 0, 0
        try:
            recs = self._records()
        except Exception as e:
            return {"ok": False, "records": 0, "bad_seq": None, "reason": f"unreadable file: {e}"}
        for rec in recs:
            n += 1
            body = {k: v for k, v in rec.items() if k not in ("hash", "prev")}
            if rec.get("seq") != prev_seq + 1:
                return {"ok": False, "records": n, "bad_seq": rec.get("seq"), "reason": "sequence gap or reorder"}
            if rec.get("prev") != prev_hash:
                return {"ok": False, "records": n, "bad_seq": rec["seq"], "reason": "previous-hash mismatch (a record was removed or changed before this one)"}
            if not hmac.compare_digest(rec.get("hash", ""), self._mac(prev_hash, body)):
                return {"ok": False, "records": n, "bad_seq": rec["seq"], "reason": "record content or key does not match its hash"}
            prev_hash, prev_seq = rec["hash"], rec["seq"]
        return {"ok": True, "records": n, "bad_seq": None, "reason": "", "head": prev_hash}

    def head(self) -> tuple[int, str]:
        recs = self._records()
        return (recs[-1]["seq"], recs[-1]["hash"]) if recs else (0, GENESIS)

    def tail(self, n: int = 50) -> list[dict]:
        return self._records()[-n:]


def get_audit(required: bool = False):
    """AuditLog using the docker secret / env key. Returns None (with a warning) when the key is missing and not required."""
    from app.common.secrets_store import default_store
    try:
        key = default_store().get("audit_hmac_key").encode()
    except KeyError:
        if required:
            raise RuntimeError("audit_hmac_key is missing: run scripts/gen_audit_key.sh and add the secret (see docs/ACCESS_AND_AUDIT.md)")
        print("WARNING: audit log disabled (no audit_hmac_key)", file=sys.stderr)
        return None
    return AuditLog(os.getenv("AUDIT_LOG", "data/audit/audit.jsonl"), key)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    log = get_audit(required=True)
    if cmd == "verify":
        r = log.verify()
        print(("AUDIT CHAIN OK" if r["ok"] else "AUDIT CHAIN BROKEN") + f": {r['records']} records" + ("" if r["ok"] else f", first problem at seq {r['bad_seq']}: {r['reason']}"))
        sys.exit(0 if r["ok"] else 1)
    if cmd == "head":
        print("seq", *log.head())
    if cmd == "tail":
        for rec in log.tail(int(sys.argv[2]) if len(sys.argv) > 2 else 20):
            print(rec["seq"], rec["ts"], rec["event"], (rec["user"] or {}).get("username"), json.dumps(rec["details"])[:100])
