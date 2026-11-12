import sqlite3
from functools import lru_cache
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from app.common.config import settings
from app.sales.nodes.agent import (execute_node, guard_node, narrate_node, select_views_node,
                                   validate_node, write_sql_node)
from app.sales.state import SalesState


def route(state):
    if not state.get("error"):
        return "ok"
    return "retry" if state.get("attempts", 0) <= settings.max_retries else "fail"


def after_plan(state):
    return "stop" if (state.get("plan") or {}).get("out_of_scope") else "go"


def build_graph(checkpoint: bool = True):
    """One compiled workflow serves every sales agent: the agent name in the state decides the scope (tables, planner, guard)."""
    g = StateGraph(SalesState)
    for name, fn in [("select_views", select_views_node), ("write_sql", write_sql_node), ("guard", guard_node),
                     ("execute", execute_node), ("validate", validate_node), ("narrate", narrate_node)]:
        g.add_node(name, fn)
    g.set_entry_point("select_views")
    g.add_conditional_edges("select_views", after_plan, {"go": "write_sql", "stop": END})
    g.add_edge("write_sql", "guard")
    g.add_conditional_edges("guard", route, {"ok": "execute", "retry": "write_sql", "fail": END})
    g.add_conditional_edges("execute", route, {"ok": "validate", "retry": "write_sql", "fail": END})
    g.add_conditional_edges("validate", route, {"ok": "narrate", "retry": "write_sql", "fail": END})
    g.add_edge("narrate", END)
    if not checkpoint:
        return g.compile()
    Path(settings.checkpoint_db).parent.mkdir(parents=True, exist_ok=True)
    return g.compile(checkpointer=SqliteSaver(sqlite3.connect(settings.checkpoint_db, check_same_thread=False)))


@lru_cache(maxsize=1)
def _agent_graph():
    return build_graph(checkpoint=False)


def run_sales_agent(agent: str, question: str, skip_narrate: bool = False) -> dict:
    return _agent_graph().invoke({"question": question, "agent": agent, "skip_narrate": skip_narrate, "attempts": 0})
