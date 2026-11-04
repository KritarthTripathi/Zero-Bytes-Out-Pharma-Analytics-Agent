import argparse
import getpass

from app.agents.hierarchy import run_question
from app.common.audit import get_audit
from app.common.auth import AuthDB
from app.common.envcheck import verify_environment
from app.common.qmask import mask_question
from app.common.tracing import init_tracing
from app.common.vault import PIIVault


def main():
    ap = argparse.ArgumentParser(description="Local pharma analytics: supervisor -> specialist agents")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ask", "sales"):                     # "sales" kept as an alias of "ask"
        s = sub.add_parser(name, help="ask a question; the supervisor decides which agent answers")
        s.add_argument("question")
        s.add_argument("--masked", action="store_true", help="show placeholders instead of real names")
        s.add_argument("--explain", action="store_true", help="show the routing decisions and the planner's query template")
        s.add_argument("--no-trace", action="store_true")
        s.add_argument("--as", dest="as_user", help="sign in as this user (asks for the password): applies the role's data scope and writes the audit log")
    args = ap.parse_args()
    try:
        env = verify_environment()
        if env["status"] == "drift":
            print(f"WARNING: environment differs from the pinned manifest ({len(env['differences'])} differences, e.g. {env['differences'][0]})")
    except Exception as e:
        print(f"WARNING: environment check failed: {e}")
        if "pinned manifest" in str(e):
            return
    if not args.no_trace:
        init_tracing()
    vault = PIIVault()
    user, audit = None, None
    if args.as_user:
        audit = get_audit(required=False)
        user, why = AuthDB().authenticate(args.as_user, getpass.getpass("Password: "), audit=audit)
        if not user:
            print("Login failed:", why)
            return
        print(f"Signed in as {user['display_name']} ({user['role']}); data scope: {user['scope']['level']} {user['scope']['value'] or ''}".rstrip())
    masked_q = mask_question(args.question, vault.load("pharma"))     # typed names/emails become the database's codes
    st = run_question(masked_q, user=user, audit=audit)
    info = st.get("route_info", {})
    if args.explain:
        sup, sal = info.get("supervisor", {}), info.get("sales")
        print(f"ROUTE: supervisor -> {sup.get('final')} ({sup.get('method')})" + (f" | sales router -> {sal.get('final')} ({sal.get('method')})" if sal else ""))
        if st.get("plan") and st["plan"].get("template"):
            print("PLAN:\n  " + "\n  ".join(st["plan"]["facts"]) + "\n  template: " + st["plan"]["template"])
        print()
    out = st.get("answer", "")
    if st.get("table"):
        out += f"\n\n{st['table']}"
    if not args.masked:                      # re-identification happens here, outside the traced graph
        out = vault.reidentify("pharma", out)
    note = (st.get("plan") or {}).get("scope_note")
    print(out + (f"\n\nSQL: {st['sql']}\n(agent: {st.get('agent')}, attempts: {st.get('attempts')}" if st.get("sql") else f"\n\n(agent: {st.get('agent')}, status: {st.get('status')}")
          + (f", results limited to: {note}" if note else "") + ")")


if __name__ == "__main__":
    main()
