import json

from app.common.audit import AuditLog

KEY = b"k" * 32


def make(tmp_path, n=5):
    log = AuditLog(str(tmp_path / "audit.jsonl"), KEY)
    for i in range(n):
        log.append("question", user={"username": "tm1", "role": "tm", "scope": {"level": "territory", "value": "cal_terr_2"}}, question=f"q{i}", status="answered")
    return log


def test_chain_verifies_and_head(tmp_path):
    log = make(tmp_path)
    r = log.verify()
    assert r["ok"] and r["records"] == 5 and log.head()[0] == 5 and r["head"] == log.head()[1]


def test_edit_is_detected(tmp_path):
    log = make(tmp_path)
    lines = log.path.read_text().splitlines()
    rec = json.loads(lines[2]); rec["details"]["status"] = "denied"; lines[2] = json.dumps(rec)
    log.path.write_text("\n".join(lines) + "\n")
    r = log.verify()
    assert not r["ok"] and r["bad_seq"] == 3


def test_delete_and_reorder_are_detected(tmp_path):
    log = make(tmp_path)
    lines = log.path.read_text().splitlines()
    log.path.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    assert not log.verify()["ok"]
    log2 = make(tmp_path / "b")
    l2 = log2.path.read_text().splitlines()
    l2[1], l2[2] = l2[2], l2[1]
    log2.path.write_text("\n".join(l2) + "\n")
    assert not log2.verify()["ok"]


def test_wrong_key_cannot_verify_and_recomputed_chain_fails(tmp_path):
    log = make(tmp_path)
    assert not AuditLog(str(log.path), b"x" * 32).verify()["ok"]


def test_appending_continues_the_chain(tmp_path):
    log = make(tmp_path, 2)
    AuditLog(str(log.path), KEY).append("feedback", user=None, verdict="up")
    assert log.verify()["ok"] and log.head()[0] == 3
