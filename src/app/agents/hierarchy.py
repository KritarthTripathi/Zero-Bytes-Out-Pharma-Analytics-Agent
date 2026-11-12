"""Supervisor -> (clinical | sales router -> sales_geo | sales_hcp | sales_activity | clarify | out_of_scope), as one LangGraph.
Questions arrive already masked (names and emails replaced by the database's codes)."""
import json
import sqlite3
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
    route: str
    sales_route: str
    route_info: dict
    status: str              # answered | clarify | out_of_scope | error
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
    con = connect()
    try:
        return load_known(con)
    finally:
        con.close()


def supervisor_node(state):
    info = route_level("supervisor", state["question"], _known(), supervisor_llm)
    tag_span(**{"route.level": "supervisor", "route.final": info["final"], "route.method": info["method"]})
    return {"route": info["final"], "route_info": {"supervisor": info}}


def sales_router_node(state):
    info = route_level("sales", state["question"], _known(), sales_router_llm)
    tag_span(**{"route.level": "sales", "route.final": info["final"], "route.method": info["method"]})
    return {"sales_route": info["final"], "route_info": {**state.get("route_info", {}), "sales": info}}


def _sales_node(agent: str):
    def node(state):
        tag_span(**{"agent.name": agent})
        st = run_sales_agent(agent, state["question"], state.get("skip_narrate", False))
        plan = st.get("plan") or {}
        base = {"agent": agent, "plan": plan, "attempts": st.get("attempts", 0)}
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
    g.set_entry_point("supervisor")
    g.add_conditional_edges("supervisor", lambda s: s["route"], {"clinical": "clinical", "sales": "sales_router", "out_of_scope": "out_of_scope", "clarify": "clarify"})
    g.add_conditional_edges("sales_router", lambda s: s["sales_route"], {"sales_geo": "sales_geo", "sales_hcp": "sales_hcp", "sales_activity": "sales_activity", "clarify": "clarify"})
    for n in ("sales_geo", "sales_hcp", "sales_activity", "clinical", "clarify", "out_of_scope"):
        g.add_edge(n, END)
    if not checkpoint:
        return g.compile()
    Path(settings.checkpoint_db).parent.mkdir(parents=True, exist_ok=True)
    return g.compile(checkpointer=SqliteSaver(sqlite3.connect(settings.checkpoint_db, check_same_thread=False)))


def run_question(question: str, skip_narrate: bool = False, checkpoint: bool = True) -> dict:
    return build_hierarchy(checkpoint).invoke({"question": question, "skip_narrate": skip_narrate},
                                              config={"configurable": {"thread_id": uuid.uuid4().hex}})
