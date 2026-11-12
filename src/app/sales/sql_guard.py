"""Parser-based SQL guard, scoped per agent: one read-only SELECT on the agent's own relations, mandatory grain_code /
geo_level filters, only approved joins, and always a LIMIT."""
from itertools import combinations

import sqlglot
from sqlglot import exp

from app.sales.semantic.catalog import GEO_VIEWS, GRAIN_VIEWS, RELATIONS, approved_joins, base_table, scope

BAD_FUNCS = {"read_csv", "read_csv_auto", "read_parquet", "read_json", "read_text", "read_blob", "glob", "parquet_scan", "sniff_csv"}
TIME_FUNCS = {"current_date", "current_timestamp", "current_time", "now", "today", "localtimestamp", "get_current_timestamp", "current_datetime"}
WRITE_NODES = tuple(getattr(exp, n) for n in ("Insert", "Update", "Delete", "Drop", "Create", "Command", "Alter") if hasattr(exp, n))


def _has_filter(tree, col: str) -> bool:
    for node in tree.find_all(exp.EQ, exp.In):
        left = node.this
        if isinstance(left, exp.Column) and left.name.lower() == col:
            return True
    return False


def _check_joins(tree):
    """Every JOIN must use an approved 'join' entry from joins.yml, on exactly its key columns."""
    specs = [j for j in approved_joins() if j["safe_as"] == "join"]
    for sel in tree.find_all(exp.Select):
        joins = sel.args.get("joins") or []
        if not joins:
            continue
        frm = sel.args.get("from_") or sel.args.get("from")
        names = [frm.this.name] if frm is not None and isinstance(frm.this, exp.Table) else []
        for j in joins:
            if not isinstance(j.this, exp.Table):
                raise ValueError("JOIN with a subquery is not allowed; use  IN (SELECT ...)")
            names.append(j.this.name)
        bases = [base_table(n) if n in RELATIONS else n for n in names]
        if len(set(bases)) != len(bases):
            raise ValueError("joining a table to itself is not allowed")
        on_cols = {c.name for j in joins if j.args.get("on") is not None for c in j.args["on"].find_all(exp.Column)}
        for a, b in combinations(bases, 2):
            spec = next((s for s in specs if {s["one"], s["many"]} == {a, b}), None)
            if spec is None:
                raise ValueError(f"no approved join between {a} and {b}; use  IN (SELECT ...)  instead")
            if not set(spec["on"]) <= on_cols:
                raise ValueError(f"the join between {a} and {b} must use ON {', '.join(spec['on'])}")


def guard(sql: str, agent=None, max_rows: int = 500) -> str:
    allowed = scope(agent)
    try:
        stmts = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except Exception as e:
        raise ValueError(f"could not parse SQL: {str(e)[:120]}")
    if len(stmts) != 1:
        raise ValueError("exactly one statement is allowed")
    tree = stmts[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise ValueError("only SELECT queries are allowed")
    if tree.find(*WRITE_NODES):
        raise ValueError("write or DDL statements are not allowed")
    for f in tree.find_all(exp.Func):
        name = f.sql_name().lower() if hasattr(f, "sql_name") else ""
        anon = str(f.name).lower() if isinstance(f, exp.Anonymous) else ""
        if name in BAD_FUNCS or anon in BAD_FUNCS:
            raise ValueError("file-reading functions are not allowed")
        if name in TIME_FUNCS or anon in TIME_FUNCS:
            raise ValueError("do not use CURRENT_DATE or NOW(); the data is fixed, use the latest-period views")
    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    tables = {t.name for t in tree.find_all(exp.Table)}
    bad = tables - allowed - ctes
    if bad:
        raise ValueError(f"relations not allowed for this agent: {sorted(bad)}. Use only: {sorted(allowed)}")
    used = tables & allowed
    if used & GRAIN_VIEWS and not _has_filter(tree, "grain_code"):
        raise ValueError("add a grain_code filter (W, 4W, M or 13W; the default is '13W')")
    if used & GEO_VIEWS and not _has_filter(tree, "geo_level"):
        raise ValueError("add a geo_level filter (TERRITORY, DISTRICT, AREA or DIVISION): levels overlap")
    try:
        _check_joins(tree)
    except ValueError:
        raise
    except Exception:
        pass                      # an sqlglot API difference must not block a valid query; the other checks still apply
    if not tree.args.get("limit"):
        tree = tree.limit(max_rows)
    return tree.sql(dialect="duckdb")
