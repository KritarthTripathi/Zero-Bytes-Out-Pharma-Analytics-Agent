"""Minimal local UI (Streamlit): sign in, ask in plain English, see the answer, route, plan and SQL. Run via docker-compose.ui.yml."""
import time

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Pharma analytics (local)", layout="wide")


@st.cache_resource
def _startup():
    out = {"status": "unchecked", "differences": []}
    try:
        from app.common.tracing import init_tracing
        init_tracing()
    except Exception:
        pass
    try:
        from app.common.envcheck import verify_environment
        out = verify_environment()
    except Exception as e:
        out = {"status": "error", "differences": [str(e)]}
    return out


env = _startup()
from app.common.access import scope_text  # noqa: E402
from app.ui import service  # noqa: E402

ss = st.session_state
ss.setdefault("user", None)
ss.setdefault("history", [])
ss.setdefault("last_active", time.time())

if ss.user and time.time() - ss.last_active > service.SESSION_SECONDS:          # idle timeout
    service.sign_out(ss.user, reason="timeout")
    ss.user, ss.history = None, []
    st.warning("You were signed out after 30 minutes of inactivity.")

if not ss.user:
    st.title("Pharma analytics: sign in")
    with st.form("login"):
        u = st.text_input("User name")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in"):
            user, why = service.sign_in(u.strip(), p)
            if user:
                ss.user, ss.last_active = user, time.time()
                st.rerun()
            st.error({"locked": "This account is locked for 15 minutes.", "locked_now": "Too many attempts: the account is locked for 15 minutes."}.get(why, "Sign-in failed."))
    st.caption("Synthetic data only. All activity is recorded in a tamper-evident audit log.")
    st.stop()

user = ss.user
ss.last_active = time.time()
with st.sidebar:
    st.subheader(user["display_name"])
    st.write(f"Role: **{user['role']}**")
    st.write(f"Data scope: **{scope_text(user['scope'])}**")
    masked = st.toggle("Show codes instead of names", value=False)
    if env.get("status") == "drift":
        st.warning("The environment differs from the pinned manifest: " + "; ".join(env["differences"][:2]))
    elif env.get("status") == "no_manifest":
        st.info("No pinned environment manifest yet.")
    if st.button("Sign out"):
        service.sign_out(user)
        ss.user, ss.history = None, []
        st.rerun()
    if "audit" in user["permissions"]:
        with st.expander("Audit log (admin)"):
            log = service.audit_log()
            if st.button("Verify chain"):
                r = log.verify()
                (st.success if r["ok"] else st.error)(f"{'Chain intact' if r['ok'] else 'CHAIN BROKEN at seq ' + str(r['bad_seq'])}: {r['records']} records. {r['reason']}")
            st.dataframe(pd.DataFrame([{"seq": r["seq"], "time": r["ts"], "event": r["event"], "user": (r["user"] or {}).get("username"),
                                        "status": r["details"].get("status")} for r in reversed(log.tail(50))]), use_container_width=True)

st.title("Ask about prescriptions, activity and territories")
st.caption("Your answers are limited to your data scope. Example: " + ("Who are the top 5 prescribers of menji in the past month?" if user["role"] != "admin" else "Which area has the least NBRx in the latest month for the Neuro division?"))


def show(res: dict, key: str, question: str):
    if res["status"] != "answered":
        st.info(res["answer"])
    else:
        if res["answer"]:
            st.write(res["answer"])
        if res["rows"]:
            st.dataframe(pd.DataFrame(res["rows"], columns=res["columns"]), use_container_width=True)
    if res["scope_note"]:
        st.caption(f"Results limited to: {res['scope_note']}")
    with st.expander("How this was answered"):
        st.write(f"Route: {res['route']}  |  agent: {res['agent']}  |  {res['seconds']} s")
        if res["plan"]:
            st.write("Plan: " + "; ".join(res["plan"]))
        if res["sql"]:
            st.code(res["sql"], language="sql")
    c1, c2, _ = st.columns([1, 1, 8])
    for col, (label, verdict) in zip((c1, c2), (("Helpful", "up"), ("Not helpful", "down"))):
        if col.button(label, key=f"{key}-{verdict}"):
            service.feedback(user, res["ref"], verdict, question)
            st.toast("Thanks, recorded.")


for i, h in enumerate(ss.history):
    with st.chat_message("user"):
        st.write(h["question"])
    with st.chat_message("assistant"):
        show(h["result"], f"h{i}", h["question"])

q = st.chat_input("Type your question")
if q:
    with st.chat_message("user"):
        st.write(q)
    with st.chat_message("assistant"):
        with st.spinner("Routing, planning and querying locally (this can take up to a minute on CPU)..."):
            res = service.ask(user, q, masked_view=masked)
        show(res, f"h{len(ss.history)}", q)
    ss.history.append({"question": q, "result": res})
