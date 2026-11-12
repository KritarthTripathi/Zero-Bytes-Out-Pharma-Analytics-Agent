"""Creates the demo users: one territory manager (tm), district manager (dm), area manager (am) and an admin.
Run from the project root: python scripts/create_users.py   (passwords are printed ONCE; re-running issues new ones)"""
import secrets

import pandas as pd

from app.common.audit import get_audit
from app.common.auth import AuthDB

al = pd.read_csv("data/schema_v2/territory_alignment_hierarchy.csv")
hs = pd.read_csv("data/schema_v2/hcp_summary_with_territory_and_tm_info.csv")
district = "Cal1 district"
cnt = hs[hs.district_name == district].drop_duplicates("hcp_ref_id").groupby("territory_name").size()
terr = cnt.idxmax()                                            # the busiest territory in the district
tm_name = al.set_index("territory_name").loc[terr, "territory_manager_name"]
area = al[al.district_name == district].area_name.iloc[0]
users = [("tm_demo", "tm", tm_name, terr), ("dm_demo", "dm", "Demo District Manager", district),
         ("am_demo", "am", "Demo Area Manager", area), ("admin_demo", "admin", "Demo Admin", None)]
db, audit = AuthDB(), get_audit(required=False)
print(f"{'user':12s} {'role':6s} {'data scope':28s} password")
for name, role, display, scope in users:
    pw = secrets.token_urlsafe(9)
    db.create_user(name, pw, role, display, scope)
    if audit:
        audit.append("user_created", user=None, username=name, role=role, scope=scope)
    print(f"{name:12s} {role:6s} {(scope or 'all'):28s} {pw}")
print("\nStore these passwords now; they are not saved anywhere else.")
