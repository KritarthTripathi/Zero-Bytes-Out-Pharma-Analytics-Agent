"""Supervisor -> (clinical | sales router -> sales_geo | sales_hcp | sales_activity | clarify | out_of_scope | denied), as one LangGraph.
Questions arrive already masked (names and emails replaced by the database's codes). A signed-in user brings a role, permissions
and a data scope; the scope is enforced inside the sales agents (scoped views) and the planner refuses what is outside it."""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from app.agents.llm_router import sales_router_llm, supervisor_llm
from app.agents.routing import FILE_RE, route_level
from app.agents.tracing_utils import tag_span
from app.common.config import settings
from app.sales.graph import run_sales_agent
from app.sales.nodes.agent import connect, load_known
from app.sales.semantic.catalog import AGENTS_CFG, SALES_AGENTS


class HState(TypedDict, total=False):
    question: str
    skip_narrate: bool
    user: dict               # username, role, permissions of the signed-in user (None = developer mode)
    scope: dict              # data scope of that user
    route: str
    sales_route: str
    route_info: dict
    status: str              # answered | clarify | out_of_scope | denied | error
    agent: str
    answer: str
    table: str
    sql: str
    plan: dict
    rows: list
    columns: list
    attempts: int
    result: dict


def _known() -> dict:
    con = connect()          # privileged read-only connection: names and the hierarchy are needed to understand any question
    try:
        return load_known(con)
    finally:
        con.close()


def supervisor_node(state):
    info = route_level("supervisor", state["question"], _known(), supervisor_llm)
    tag_span(**{"route.level": "supervisor", "route.final": info["final"], "route.method": info["method"]})
    out = {"route": info["final"], "route_info": {"supervisor": info}}
    user = state.get("user")
    if user and info["final"] in ("clinical", "sales") and info["final"] not in user.get("permissions", []):
        out.update({"route": "denied", "answer": f"Your role ({user.get('role')}) is not permitted to use the {info['final']} agent."})
    return out


def sales_router_node(state):
    info = route_level("sales", state["question"], _known(), sales_router_llm)
    tag_span(**{"route.level": "sales", "route.final": info["final"], "route.method": info["method"]})
    return {"sales_route": info["final"], "route_info": {**state.get("route_info", {}), "sales": info}}


def _sales_node(agent: str):
    def node(state):
        tag_span(**{"agent.name": agent})
        st = run_sales_agent(agent, state["question"], state.get("skip_narrate", False), scope=state.get("scope"))
        plan = st.get("plan") or {}
        base = {"agent": agent, "plan": plan, "attempts": st.get("attempts", 0)}
        if plan.get("denied"):
            return {**base, "status": "denied", "answer": plan["message"]}
        if plan.get("out_of_scope"):
            return {**base, "status": "out_of_scope", "answer": plan["message"]}
        if st.get("error"):
            return {**base, "status": "error", "answer": f"Could not answer after {st.get('attempts')} attempts: {st['error']}", "sql": st.get("sql_raw", "")}
        return {**base, "status": "answered", "answer": st.get("answer", ""), "table": st.get("table", ""), "sql": st.get("sql", ""),
                "rows": st.get("rows", []), "columns": st.get("columns", [])}
    node.__name__ = agent
    return node


def clinical_node(state):
    tag_span(**{"agent.name": "clinical"})
    m = FILE_RE.search(state["question"])
    if not m:
        return {"agent": "clinical", "status": "clarify", "answer": "I can parse a clinical-trial protocol, but I need the file path (a .pdf, .txt or .md file), for example data/synthetic/protocol_001.txt."}
    from app.clinical.graph import build_graph as build_clinical
    st = build_clinical().invoke({"input_path": m.group(0)}, config={"configurable": {"thread_id": uuid.uuid4().hex}})
    comp = st.get("compliance", {})
    if comp.get("verdict") != "pass":
        return {"agent": "clinical", "status": "error", "answer": f"Blocked by the compliance check: {comp.get('reason')}", "result": {"compliance": comp}}
    return {"agent": "clinical", "status": "answered", "answer": json.dumps(st.get("result"), indent=2),
            "result": {"validation": st.get("validation"), "compliance": comp}}


def clarify_node(state):
    info = state.get("route_info", {})
    opts = (info.get("sales") or info.get("supervisor") or {}).get("options", [])
    desc = {**{k: v for k, v in AGENTS_CFG["supervisor"]["routes"].items()}, **{k: v["description"] for k, v in SALES_AGENTS.items()}}
    lines = [f"- {o}: {desc.get(o, '')}" for o in opts]
    return {"status": "clarify", "answer": "I am not sure how to handle that. " + ("It could be:\n" + "\n".join(lines) + "\n" if lines else "") +
            "Please rephrase with the metric (TRx, NBRx, NTS, reach, VAE) and what you want to compare."}


def out_of_scope_node(state):
    avail = "\n".join(f"- {k}: {v['description']}" for k, v in SALES_AGENTS.items() if k != "sales_all")
    return {"status": "out_of_scope", "answer": "That is outside the data I have. I can answer questions in these areas:\n" + avail +
            "\n- clinical: parse a clinical-trial protocol file."}


def denied_node(state):
    return {"status": "denied", "answer": state.get("answer", "Access denied.")}


@lru_cache(maxsize=2)
def build_hierarchy(checkpoint: bool = True):
    g = StateGraph(HState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("sales_router", sales_router_node)
    for a in ("sales_geo", "sales_hcp", "sales_activity"):
        g.add_node(a, _sales_node(a))
    g.add_node("clinical", clinical_node)
    g.add_node("clarify", clarify_node)
    g.add_node("out_of_scope", out_of_scope_node)
    g.add_node("denied", denied_node)
    g.set_entry_point("supervisor")
    g.add_conditional_edges("supervisor", lambda s: s["route"], {"clinical": "clinical", "sales": "sales_router", "out_of_scope": "out_of_scope",
                                                                  "clarify": "clarify", "denied": "denied"})
    g.add_conditional_edges("sales_router", lambda s: s["sales_route"], {"sales_geo": "sales_geo", "sales_hcp": "sales_hcp", "sales_activity": "sales_activity", "clarify": "clarify"})
    for n in ("sales_geo", "sales_hcp", "sales_activity", "clinical", "clarify", "out_of_scope", "denied"):
        g.add_edge(n, END)
    if not checkpoint:
        return g.compile()
    Path(settings.checkpoint_db).parent.mkdir(parents=True, exist_ok=True)
    return g.compile(checkpointer=SqliteSaver(sqlite3.connect(settings.checkpoint_db, check_same_thread=False)))


@lru_cache(maxsize=1)
def _env() -> dict:
    try:
        from app.common.envcheck import verify_environment
        return verify_environment()
    except Exception:
        return {}


def result_hash(rows) -> str:
    return hashlib.sha256(json.dumps(rows or [], sort_keys=True, default=str).encode()).hexdigest()


def _audit(audit, user, question, st, seconds):
    info = st.get("route_info", {})
    sup, sal = info.get("supervisor", {}), info.get("sales") or {}
    audit.append("access_denied" if st.get("status") == "denied" else "question", user=user, question=question[:500], status=st.get("status"),
                 agent=st.get("agent"), supervisor=f"{sup.get('final')}/{sup.get('method')}",
                 sales_router=(f"{sal.get('final')}/{sal.get('method')}" if sal else None), sql=(st.get("sql") or "")[:1500],
                 rows=len(st.get("rows") or []), result_sha256=result_hash(st.get("rows")), seconds=round(seconds, 1),
                 manifest=_env().get("manifest_hash"), code_version=os.getenv("APP_VERSION", "dev"))


def run_question(question: str, skip_narrate: bool = False, checkpoint: bool = True, user: dict | None = None, audit=None) -> dict:
    """user: signed-in user from app.common.auth (None = developer mode, unrestricted). audit: AuditLog or None."""
    init = {"question": question, "skip_narrate": skip_narrate}
    if user:
        init["user"] = {k: user[k] for k in ("username", "role", "permissions")}
        init["scope"] = user.get("scope")
    t0 = time.time()
    st = build_hierarchy(checkpoint).invoke(init, config={"configurable": {"thread_id": uuid.uuid4().hex}})
    if audit is not None:
        _audit(audit, user, question, st, time.time() - t0)
    return st
