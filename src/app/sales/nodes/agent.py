"""Sales agent steps (schema v2). The database holds only masked values and the question is masked before it gets here."""
import json
import os
import re
from decimal import Decimal
from pathlib import Path

import duckdb

from app.common.access import SCOPE_COL, build_access, is_restricted, predicate_for
from app.common.config import settings
from app.common.llm import get_llm
from app.sales.narration import plain_summary, valid_narration
from app.sales.semantic.catalog import GEO_VIEWS, RELATIONS, make_plan, render_prompt
from app.sales.sql_guard import guard

DB = Path(settings.work_dir) / "pharma.duckdb"
COUNT_COLS = re.compile(r"(trx|nbrx|nts|previous_\w+|remaining|suggested_engagements|activity_count|total\w*)")


def connect(scope=None):
    """Read-only connection. For a restricted user the model's SQL runs in an in-memory database that only contains
    scope-filtered views of the approved relations; the real file is attached read-only under the name `base`
    (the guard rejects qualified names such as base.table, so the unfiltered data cannot be reached)."""
    if not is_restricted(scope):
        con = duckdb.connect(str(DB), read_only=True)
        try:
            con.execute("SET enable_external_access=false")
        except Exception:
            pass
        return con
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{DB}' AS base (READ_ONLY)")
    for rel in RELATIONS:
        con.execute(f"CREATE VIEW {rel} AS SELECT * FROM base.{rel} WHERE {predicate_for(scope, rel in GEO_VIEWS)}")
    try:
        con.execute("SET enable_external_access=false")
    except Exception:
        pass
    return con


def load_access(con, scope):
    """Hierarchy facts needed to refuse out-of-scope questions (None for unrestricted users). Uses the privileged connection."""
    if not is_restricted(scope):
        return None
    rows = con.execute("SELECT area_name, district_name, territory_name, division_name, territory_manager_name, territory_manager_email FROM territory_alignment_hierarchy").fetchall()
    hcps = [r[0] for r in con.execute(f"SELECT DISTINCT hcp_full_name FROM hcp_summary_with_territory_and_tm_info WHERE {SCOPE_COL[scope['level']]} = ?", [scope["value"]]).fetchall()]
    return build_access(scope, rows, hcps)


def _json_default(o):
    return float(o) if isinstance(o, Decimal) else str(o)


def load_known(con) -> dict:
    q = lambda sql: [r[0] for r in con.execute(sql).fetchall()]
    return {"territory_name": q("SELECT DISTINCT territory_name FROM territory_alignment_hierarchy"),
            "district_name": q("SELECT DISTINCT district_name FROM territory_alignment_hierarchy"),
            "area_name": q("SELECT DISTINCT area_name FROM territory_alignment_hierarchy"),
            "division_name": q("SELECT DISTINCT division_name FROM territory_alignment_hierarchy"),
            "product_name": q("SELECT DISTINCT product_name FROM hcp_summary_with_territory_and_tm_info"),
            "address_line": q("SELECT DISTINCT address_line FROM hcp_summary_with_territory_and_tm_info")}


def extract_sql(text: str) -> str:
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    text = m.group(1) if m else text
    m = re.search(r"\b(select|with)\b", text, re.I)
    return text[m.start():].strip() if m else text.strip()


def select_views_node(state):
    con = connect()
    known = load_known(con)
    access = load_access(con, state.get("scope"))
    data_end = str(con.execute("SELECT MAX(grain_end_date) FROM hcp_rx_summary_report").fetchone()[0])
    con.close()
    plan = make_plan(state["question"], known, state.get("agent"), access)
    return {"plan": plan, "views": plan["relations"], "data_end": data_end, "attempts": 0, "error": ""}


def write_sql_node(state):
    attempts = state.get("attempts", 0) + 1
    if os.getenv("PROMPT_MODE", "full") == "template" and not state.get("error"):   # ablation: planner only, no LLM
        return {"sql_raw": state["plan"]["template"], "attempts": attempts, "error": ""}
    prompt = render_prompt(state["plan"], state["question"])
    if state.get("error"):
        prompt += f" {state.get('sql_raw', '')[:400]}\n(That attempt failed: {state['error']}. Write a corrected query.)\nSQL:"
    raw = get_llm("sql").invoke(prompt).content
    return {"sql_raw": extract_sql(raw), "attempts": attempts, "error": ""}


def guard_node(state):
    try:
        return {"sql": guard(state["sql_raw"], state.get("agent")), "error": ""}
    except Exception as e:
        return {"error": f"SQL rejected: {e}"}


def execute_node(state):
    try:
        con = connect(state.get("scope"))
        res = con.execute(state["sql"])
        cols = [d[0] for d in res.description]
        rows = res.fetchmany(200)
        con.close()
        return {"columns": cols, "rows": json.loads(json.dumps(rows, default=_json_default)), "error": ""}
    except Exception as e:
        return {"error": f"SQL error: {str(e)[:300]}"}


def validate_node(state):
    rows, cols = state.get("rows", []), state.get("columns", [])
    if not rows or all(v is None for r in rows for v in r):
        if is_restricted(state.get("scope")):             # nothing inside the user's scope: answer plainly, do not retry
            return {"error": "", "rows": [], "columns": cols}
        return {"error": "query returned no rows or only NULL; check filters and the grain"}
    for j, c in enumerate(cols):
        if COUNT_COLS.fullmatch(c.lower()) and any(isinstance(r[j], (int, float)) and r[j] < 0 for r in rows):
            return {"error": f"negative values in count column {c}"}
    return {"error": ""}


def _fmt(v):
    return round(v, 4) if isinstance(v, float) else v


def render_table(cols, rows, n=20):
    lines = [" | ".join(cols)] + [" | ".join(str(_fmt(v)) for v in r) for r in rows[:n]]
    if len(rows) > n:
        lines.append(f"... {len(rows) - n} more rows")
    return "\n".join(lines)


def _nums(s):
    out = []
    for x in re.findall(r"(?<![A-Za-z0-9_])\d[\d,]*\.?\d*", s):
        try:
            out.append(float(x.replace(",", "").rstrip(".")))
        except ValueError:
            pass
    return out


def grounded(narration, table):
    allowed = _nums(table)
    return all(any(abs(x - y) <= max(0.01 * abs(y), 0.006) for y in allowed) or (x <= 10 and x == int(x)) for x in _nums(narration))


def narrate_node(state):
    if not state.get("rows"):
        return {"table": "", "answer": "No matching data within your access scope for that question."}
    table = render_table(state["columns"], state["rows"])
    if state.get("skip_narrate"):
        return {"table": table, "answer": ""}
    prompt = (f"Question: {state['question']}\nResults:\n{table}\n"
              "Answer in one or two short sentences using only numbers shown in the results. "
              "If the results contain codes in angle brackets, copy them exactly; never write a code that is not in the results.")
    text = get_llm("router").invoke(prompt).content.strip()
    return {"table": table, "answer": text if valid_narration(text, table) else plain_summary(state["columns"], state["rows"])}
