import sqlite3

from app.common.auth import AuthDB, hash_password, verify_password


def test_password_hashing():
    s, h = hash_password("S3cret-pass")
    assert verify_password("S3cret-pass", s, h) and not verify_password("wrong", s, h)
    assert "S3cret" not in h and len(h) == 64


def test_login_lockout_and_roles(tmp_path):
    db = AuthDB(str(tmp_path / "u.db"))
    db.create_user("tm1", "pw-tm", "tm", "Tina TM", "cal_terr_2")
    db.create_user("adm", "pw-adm", "admin", "Ada")
    u, why = db.authenticate("tm1", "pw-tm")
    assert why == "ok" and u["role"] == "tm" and u["scope"] == {"level": "territory", "value": "cal_terr_2"} and u["permissions"] == ["sales"]
    assert db.authenticate("adm", "pw-adm")[0]["permissions"] == ["sales", "clinical", "audit"]
    assert db.authenticate("nobody", "x")[1] == "unknown_or_inactive"
    for i in range(4):
        assert db.authenticate("tm1", "bad", now=1000.0)[1] == "bad_password"
    assert db.authenticate("tm1", "bad", now=1000.0)[1] == "locked_now"
    assert db.authenticate("tm1", "pw-tm", now=1001.0)[1] == "locked"             # correct password still refused while locked
    assert db.authenticate("tm1", "pw-tm", now=1000.0 + 901)[1] == "ok"
    raw = sqlite3.connect(tmp_path / "u.db").execute("SELECT salt, pw_hash FROM users WHERE username = 'tm1'").fetchone()
    assert "pw-tm" not in "".join(raw)


def test_scoped_roles_need_a_scope(tmp_path):
    db = AuthDB(str(tmp_path / "u.db"))
    try:
        db.create_user("dm1", "x", "dm")
        assert False
    except ValueError:
        pass
