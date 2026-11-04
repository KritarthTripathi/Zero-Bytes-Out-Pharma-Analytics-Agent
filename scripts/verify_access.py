"""Access check on the real database (DuckDB): every role sees only its own scope, totals match the administrator's, and the
guard blocks the way around the scoped views. Run: python scripts/verify_access.py"""
import sys

from app.common.access import SCOPE_COL
from app.common.auth import AuthDB
from app.sales.nodes.agent import connect
from app.sales.semantic.catalog import RELATIONS
from app.sales.sql_guard import guard

ok_all = True


def check(name, cond, detail=""):
    global ok_all
    ok_all = ok_all and bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{detail}]" if detail else ""))


adm = connect(None)
al = adm.execute("SELECT area_name, district_name, territory_name FROM territory_alignment_hierarchy").fetchall()
idx = {"area": 0, "district": 1, "territory": 2}
users = [u for u in AuthDB().list_users() if u["role"] in ("tm", "dm", "am")]
check("demo users exist for tm, dm and am", {u["role"] for u in users} == {"tm", "dm", "am"})
for u in users:
    scope, tag = u["scope"], f"{u['role']} ({u['scope']['value']})"
    con, col = connect(scope), SCOPE_COL[u["scope"]["level"]]
    mine = {r[2] for r in al if r[idx[scope["level"]]] == scope["value"]}
    for rel in RELATIONS:
        got = {r[0] for r in con.execute(f"SELECT DISTINCT territory_name FROM {rel}").fetchall()} - {"ALL"}
        check(f"{tag}: {rel} holds only in-scope territories", got <= mine, f"{len(got)} of {len(mine)} territories")
    for g in ("W", "13W"):
        a = con.execute(f"SELECT COALESCE(SUM(trx), 0) FROM v_hcp_rx_latest WHERE grain_code = '{g}'").fetchone()[0]
        b = adm.execute(f"SELECT COALESCE(SUM(trx), 0) FROM v_hcp_rx_latest WHERE grain_code = '{g}' AND {col} = ?", [scope["value"]]).fetchone()[0]
        check(f"{tag}: HCP TRx total {g} equals the administrator's total for the same scope", a == b, f"{a} vs {b}")
    lv = {r[0] for r in con.execute("SELECT DISTINCT geo_level FROM v_geo_latest").fetchall()}
    want = {"territory": {"TERRITORY"}, "district": {"TERRITORY", "DISTRICT"}, "area": {"TERRITORY", "DISTRICT", "AREA"}}[scope["level"]]
    check(f"{tag}: geo levels visible are {sorted(want)}", lv == want, str(sorted(lv)))
for sql in ("SELECT SUM(trx) FROM base.v_hcp_rx_latest WHERE grain_code = '13W'", "SELECT * FROM base.main.hcp_summary_with_territory_and_tm_info"):
    try:
        guard(sql, "sales_hcp")
        check("guard blocks schema-qualified access to the unfiltered data", False, sql)
    except ValueError:
        check("guard blocks schema-qualified access to the unfiltered data", True)
print("\nALL ACCESS CHECKS PASSED" if ok_all else "\nSOME ACCESS CHECKS FAILED")
sys.exit(0 if ok_all else 1)
