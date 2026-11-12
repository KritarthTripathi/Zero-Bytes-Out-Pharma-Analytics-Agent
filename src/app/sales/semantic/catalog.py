"""Planner for schema v2 with scoped agents. Turns a (masked) question into resolved facts and a query template using
metrics.yml, agents.yml and joins.yml. Deterministic and unit-tested; the local LLM then completes and writes the SQL."""
import os
import re
from pathlib import Path

import yaml

_DIR = Path(__file__).parent
CATALOG = yaml.safe_load((_DIR / "metrics.yml").read_text())
AGENTS_CFG = yaml.safe_load((_DIR / "agents.yml").read_text())
JOINS = yaml.safe_load((_DIR / "joins.yml").read_text()) if (_DIR / "joins.yml").exists() else {"joins": []}
SALES_AGENTS = AGENTS_CFG["sales_agents"]
RELATIONS = CATALOG["relations"]
ALLOWED = set(RELATIONS)
GRAIN_VIEWS = {k for k, v in RELATIONS.items() if v.get("needs_grain")}
GEO_VIEWS = {k for k, v in RELATIONS.items() if v.get("needs_geo_level")}
D = CATALOG["defaults"]
SUMMARY, ALIGN = "hcp_summary_with_territory_and_tm_info", "territory_alignment_hierarchy"
TM_COLS = {"territory_manager_name", "territory_manager_email"}
SEMI_COLS = {"address_line", "product_name"}
PLACEHOLDER_COL = {"HCP_FULL_NAME": "hcp_full_name", "TERRITORY_MANAGER_NAME": "territory_manager_name",
                   "TERRITORY_MANAGER_EMAIL": "territory_manager_email"}
LEVEL_COL = {"TERRITORY": "territory_name", "DISTRICT": "district_name", "AREA": "area_name", "DIVISION": "division_name"}
LEVEL_VALID = {"TERRITORY": {"territory_name", "district_name", "area_name", "division_name", "product_name"},
               "DISTRICT": {"district_name", "area_name", "division_name", "product_name"},
               "AREA": {"area_name", "division_name", "product_name"},
               "DIVISION": {"division_name", "product_name"}}
GEO_WORDS = r"(territory|territories|district|districts|area|areas|division|divisions)"
ACTIVITY_METRICS = {"reach_pct", "vae_status_pct", "remaining_calls", "remaining_vae"}
RX_METRICS = {"trx", "nbrx", "nts"}


def base_table(rel: str) -> str:
    return RELATIONS[rel].get("base_table", rel)


def scope(agent):
    """Relations an agent may query. None / sales_all = everything (the earlier single-agent design)."""
    if agent in (None, "sales_all"):
        return set(ALLOWED)
    return set(SALES_AGENTS[agent]["relations"])


def approved_joins():
    return [j for j in JOINS.get("joins", []) if j.get("approved")]


def join_hints(agent) -> list[str]:
    tabs = {base_table(r) for r in scope(agent)}
    lines = []
    for j in approved_joins():
        if j["one"] in tabs and j["many"] in tabs:
            if j["safe_as"] == "join":
                lines.append(f"A JOIN of {j['one']} and {j['many']} is allowed only ON {', '.join(j['on'])}.")
            else:
                lines.append(f"Never JOIN {j['one']} with {j['many']}. Semi-join only: {j['pattern']}")
    return lines or ["There are no joins between your tables: never use JOIN. Use the territory lookup subquery for managers."]


# ------------------------------------------------------------------ resolvers
def resolve_grain(q: str) -> tuple[str, bool]:
    q = q.lower()
    if re.search(r"\b(4|four)[- ]?weeks?\b|\b4w\b", q): return "4W", True
    if re.search(r"\b(13|thirteen)[- ]?weeks?\b|\b13w\b|\bquarter\b", q): return "13W", True
    if re.search(r"\bmonth(ly)?\b", q): return "M", True
    if re.search(r"\bweek(ly)?\b", q): return "W", True
    return D["grain_code"], False


REMAIN = r"\b(suggested|have to make|remaining|remain\w*|still need|need to (?:call|email|send)|left to)\b"


def resolve_metric(q: str) -> str:
    q = q.lower()
    if re.search(r"\bvae\b", q) and re.search(REMAIN, q): return "remaining_vae"
    if re.search(REMAIN, q) and re.search(r"\bcalls?\b", q): return "remaining_calls"
    if re.search(r"\bvae\b", q) and re.search(r"\b(status|reach)\b", q): return "vae_status_pct"
    if re.search(r"\breach\b", q): return "reach_pct"
    for m in ("nbrx", "nts", "trx"):
        if re.search(rf"\b{m}\b", q): return m
    return D["rank_metric"]


def resolve_order(q: str) -> tuple[str, int | None]:
    q = q.lower()
    asc = bool(re.search(r"\b(least|lowest|fewest|smallest|bottom|declin\w*|falling|dropping|decreas\w*)\b", q))
    m = re.search(r"\b(?:top|bottom|first|which)\s+(\d+)\b", q)
    n = int(m.group(1)) if m else None
    if n is None:
        if re.search(r"\b(top|bottom)\b", q): n = D["list_limit"]
        elif re.search(r"\b(most|least|highest|lowest|fewest|best)\b", q): n = 1
    return ("ASC" if asc else "DESC"), n


def ask_level(q: str) -> str | None:
    ql = q.lower()
    m = re.search(rf"\bwhich\s+(?:\d+\s+)?{GEO_WORDS}\b", ql) or re.search(rf"\b(?:top|bottom)\s+(?:\d+\s+)?{GEO_WORDS}\b", ql)
    if not m:
        return None
    w = m.group(1)
    return "TERRITORY" if w.startswith("territor") else "DISTRICT" if w.startswith("district") else "AREA" if w.startswith("area") else "DIVISION"


def link_entities(question: str, known: dict) -> list[tuple[str, str]]:
    """Find known values in the question: masked placeholders (<ENTITY_n>) and exact names of territories, districts, areas, divisions, products, addresses."""
    found = []
    for m in re.finditer(r"<([A-Z_]+)_\d+>", question):
        col = PLACEHOLDER_COL.get(m.group(1))
        if col and (col, m.group(0)) not in found:
            found.append((col, m.group(0)))
    ql = question.lower()
    for col, values in known.items():
        hit = None
        for v in sorted(values, key=len, reverse=True):
            alts = [v]
            if col == "area_name" and v.endswith(" Area"): alts.append(v[:-5])
            if col == "district_name" and v.endswith(" district"): alts.append(v[:-9])
            if any(re.search(r"(?<!\w)" + re.escape(a.lower()) + r"(?!\w)", ql) for a in alts):
                hit = v
                break
        if hit:
            found.append((col, hit))
    return found


def _flags(q: str, ents: list):
    ql = q.lower()
    return {"cols": {c for c, _ in ents},
            "metric_explicit": bool(re.search(r"\b(trx|nbrx|nts|prescriptions?|scripts?)\b", ql)),
            "ranking": bool(re.search(r"\b(most|least|highest|lowest|fewest|top|bottom|best)\b", ql)),
            "hcp_words": bool(re.search(r"\b(who|hcps?|doctors?|prescribers?)\b", ql)),
            "trending": bool(re.search(r"\btrend", ql)), "lvl": ask_level(q)}


def select_relations(q: str, ents: list, metric: str, agent=None) -> list[str]:
    f = _flags(q, ents)
    if agent in (None, "sales_all"):                         # earlier single-agent behaviour
        if metric in ACTIVITY_METRICS: return ["v_activity_latest"]
        if f["trending"]: return ["v_geo_trend" if f["lvl"] else "v_hcp_rx_trend"]
        if "address_line" in f["cols"] or (f["hcp_words"] and not f["ranking"] and not f["metric_explicit"]): return [SUMMARY]
        if f["lvl"] and not f["metric_explicit"] and not f["ranking"]: return [ALIGN]
        if f["lvl"] or (not f["hcp_words"] and f["cols"] & set(LEVEL_COL.values()) and f["metric_explicit"]): return ["v_geo_latest"]
        return ["v_hcp_rx_latest"]
    if agent == "sales_activity":                            # scoped agents refuse what is outside their tables
        return ["v_activity_latest"] if metric in ACTIVITY_METRICS else []
    if metric in ACTIVITY_METRICS:
        return []
    if agent == "sales_geo":
        if "address_line" in f["cols"]: return []
        if f["trending"]: return ["v_geo_trend"]
        if f["lvl"] and not f["metric_explicit"] and not f["ranking"]: return [ALIGN]
        return ["v_geo_latest"]
    if agent == "sales_hcp":
        if f["lvl"] and not f["hcp_words"]: return []
        if f["trending"]: return ["v_hcp_rx_trend"]
        if "address_line" in f["cols"]: return ["v_hcp_rx_latest"] if (f["ranking"] or f["metric_explicit"]) else [SUMMARY]
        if f["hcp_words"] and not f["ranking"] and not f["metric_explicit"]: return [SUMMARY]
        return ["v_hcp_rx_latest"]
    return []


# ------------------------------------------------------------------ plan
def _and(parts):
    return "".join(f" AND {p}" for p in parts)


def make_plan(question: str, known: dict, agent=None) -> dict:
    ents = link_entities(question, known)
    metric = resolve_metric(question)
    rels = select_relations(question, ents, metric, agent)
    if not rels:
        return {"out_of_scope": True, "agent": agent, "relations": [], "facts": [], "template": "", "grain": None, "metric": metric, "level": None,
                "entities": [list(e) for e in ents],
                "message": f"This question is outside what the {agent} agent can answer from its tables."}
    primary = rels[0]
    grain, grain_stated = resolve_grain(question)
    direction, n = resolve_order(question)
    asked = ask_level(question)
    relcols = set(RELATIONS[primary]["columns"])
    has_tm = any(c in TM_COLS for c, _ in ents)
    level = None
    if primary in GEO_VIEWS:
        level = asked or ("TERRITORY" if has_tm else next((L for L, c in (("TERRITORY", "territory_name"), ("DISTRICT", "district_name"), ("AREA", "area_name"), ("DIVISION", "division_name")) if any(e[0] == c for e in ents)), "TERRITORY"))
    filters, facts, uses_summary = [], [], False
    for c, v in ents:
        if c in TM_COLS and primary not in (SUMMARY, ALIGN):
            filters.append(f"territory_name = (SELECT territory_name FROM {ALIGN} WHERE {c} = '{v}')")
        elif c in relcols and (level is None or c in LEVEL_VALID[level]):
            filters.append(f"{c} = '{v}'")
        elif c in SEMI_COLS and primary in ("v_activity_latest", "v_hcp_rx_latest", "v_hcp_rx_trend") and SUMMARY in scope(agent):
            filters.append(f"hcp_ref_id IN (SELECT hcp_ref_id FROM {SUMMARY} WHERE {c} = '{v}')")
            uses_summary = True
    facts.append(f"Use {primary}." + (" It already holds only the latest period (no date filter)." if primary in GRAIN_VIEWS else ""))
    if primary in GRAIN_VIEWS:
        facts.append(f"grain_code = '{grain}' ({CATALOG['grains'][grain]})" + ("" if grain_stated else " - the default"))
    if level:
        facts.append(f"geo_level = '{level}'" + (f"; group by {LEVEL_COL[level]}" if asked else ""))
    if filters:
        facts.append("Filters: " + "; ".join(filters))
    where_f = _and(filters)
    lim = lambda k: f" LIMIT {k}" if k else ""
    if metric in ACTIVITY_METRICS:
        mt = CATALOG["metrics"][metric]
        base = f"FROM v_activity_latest WHERE {mt['filter']} AND grain_code = '{grain}'{where_f}"
        facts.append(f"Metric: {mt['label']} = {mt['sql']}")
        t = (f"SELECT hcp_ref_id, hcp_full_name, {mt['sql']} AS remaining {base} ORDER BY remaining DESC LIMIT 10"
             if metric.startswith("remaining") else f"SELECT {mt['sql']} AS {metric} {base}")
    elif primary == "v_hcp_rx_trend":
        facts.append("Trending = trx_change_pct (percent change versus the previous period), previous_trx >= 10.")
        k = n or D["list_limit"]
        t = (f"SELECT hcp_ref_id, hcp_full_name, product_name, trx, previous_trx, trx_change_pct FROM v_hcp_rx_trend "
             f"WHERE grain_code = '{grain}' AND previous_trx >= {D['min_previous_volume_for_trending']}{where_f} ORDER BY trx_change_pct {direction} LIMIT {k}")
    elif primary == "v_geo_trend":
        lc = LEVEL_COL[level]
        facts.append("Trending = percent change of SUM(trx) versus SUM(previous_trx).")
        t = (f"SELECT {lc}, SUM(trx) AS trx, SUM(previous_trx) AS previous_trx, (SUM(trx) - SUM(previous_trx)) * 100.0 / NULLIF(SUM(previous_trx), 0) AS trx_change_pct "
             f"FROM v_geo_trend WHERE geo_level = '{level}' AND grain_code = '{grain}'{where_f} GROUP BY {lc} "
             f"HAVING SUM(previous_trx) >= {D['min_previous_volume_for_trending']} ORDER BY trx_change_pct {direction}{lim(n or D['list_limit'])}")
    elif primary == "v_geo_latest":
        lc = LEVEL_COL[level]
        facts.append(f"Metric: {metric} (SUM({metric}))" + (f"; order {direction}" + (f"; limit {n}" if n else "") if asked else ""))
        t = (f"SELECT {lc}, SUM({metric}) AS {metric} FROM v_geo_latest WHERE geo_level = '{level}' AND grain_code = '{grain}'{where_f} GROUP BY {lc} ORDER BY {metric} {direction}{lim(n)}"
             if asked else f"SELECT SUM({metric}) AS {metric} FROM v_geo_latest WHERE geo_level = '{level}' AND grain_code = '{grain}'{where_f}")
    elif primary == "v_hcp_rx_latest":
        facts.append(f"Metric: {metric} (SUM({metric})); order {direction}" + (f"; limit {n}" if n else ""))
        t = (f"SELECT hcp_ref_id, hcp_full_name, SUM({metric}) AS {metric} FROM v_hcp_rx_latest WHERE grain_code = '{grain}'{where_f} "
             f"GROUP BY hcp_ref_id, hcp_full_name ORDER BY {metric} {direction}{lim(n or D['list_limit'])}")
    elif primary == SUMMARY:
        t = f"SELECT DISTINCT hcp_ref_id, hcp_full_name FROM {SUMMARY}" + (" WHERE " + " AND ".join(filters) if filters else "")
    else:
        t = f"SELECT * FROM {ALIGN}" + (" WHERE " + " AND ".join(filters) if filters else "")
    extra = ([ALIGN] if has_tm and primary not in (SUMMARY, ALIGN) else []) + ([SUMMARY] if uses_summary and primary != SUMMARY else [])
    return {"out_of_scope": False, "agent": agent, "relations": [primary] + extra, "facts": facts, "template": t,
            "grain": grain, "metric": metric, "level": level, "entities": [list(e) for e in ents]}


def render_prompt(plan: dict, question: str, mode: str | None = None) -> str:
    mode = mode or os.getenv("PROMPT_MODE", "full")      # "full" = facts + query template, "facts" = facts only (for ablation)
    agent = plan.get("agent")
    persona = SALES_AGENTS.get(agent, {}).get("persona", "") if agent else ""
    rel = "\n".join(f"- {r}: {RELATIONS[r]['description']} Columns: {', '.join(RELATIONS[r]['columns'])}" for r in plan["relations"])
    rules = list(CATALOG["global_rules"]) + [x for r in plan["relations"] for x in RELATIONS[r].get("rules", [])] + join_hints(agent)
    out = ((persona + "\n" if persona else "") + "You write one DuckDB SQL query.\nRelations you may use:\n" + rel +
           "\nFacts resolved from the question (follow them exactly):\n" + "\n".join("- " + f for f in plan["facts"]) +
           "\nRules:\n" + "\n".join("- " + r for r in rules))
    if mode == "full":
        out += "\nQuery template (copy it; change only what the question clearly needs):\n" + plan["template"]
    return out + f"\nQ: {question}\nSQL:"
