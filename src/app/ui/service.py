"""UI helpers without any Streamlit import (so they can be reused and tested): sign-in, asking a question as a user, feedback."""
import json
import time
from functools import lru_cache

from app.agents.hierarchy import run_question
from app.common.audit import get_audit
from app.common.auth import AuthDB
from app.common.qmask import PH, mask_question, safe_reidentify
from app.common.vault import PIIVault

SESSION_SECONDS = 1800


@lru_cache(maxsize=1)
def audit_log():
    return get_audit(required=True)          # the UI refuses to run without the audit key


def sign_in(username: str, password: str):
    return AuthDB().authenticate(username, password, audit=audit_log())


def sign_out(user: dict, reason: str = "user"):
    audit_log().append("logout", user=user, reason=reason)


def ask(user: dict, question: str, masked_view: bool = False) -> dict:
    vault = PIIVault()
    masked_q = mask_question(question, vault.load("pharma"))
    t0 = time.time()
    st = run_question(masked_q, checkpoint=False, user=user, audit=audit_log())
    allowed = set(PH.findall((st.get("table") or "") + json.dumps(st.get("rows") or [])))      # only codes that are part of the result
    mapping = vault.load("pharma")
    show = (lambda x: x) if masked_view else (lambda x: safe_reidentify(x, mapping, allowed) if isinstance(x, str) else x)
    info = st.get("route_info", {})
    sup, sal = info.get("supervisor", {}), info.get("sales")
    return {"answer": show(st.get("answer", "")), "columns": st.get("columns") or [], "rows": [[show(v) for v in r] for r in (st.get("rows") or [])],
            "sql": st.get("sql", ""), "status": st.get("status"), "agent": st.get("agent"), "scope_note": (st.get("plan") or {}).get("scope_note"),
            "route": f"supervisor: {sup.get('final')} ({sup.get('method')})" + (f" -> {sal.get('final')} ({sal.get('method')})" if sal else ""),
            "plan": (st.get("plan") or {}).get("facts", []), "seconds": round(time.time() - t0, 1), "ref": audit_log().head()[0]}


def feedback(user: dict, ref: int, verdict: str, question: str):
    audit_log().append("feedback", user=user, ref=ref, verdict=verdict, question=mask_question(question, PIIVault().load("pharma"))[:300])
