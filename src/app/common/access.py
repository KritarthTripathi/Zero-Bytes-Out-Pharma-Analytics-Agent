"""Role-based, row-level access for territory managers (tm), district managers (dm), area managers (am) and admin.
Pure functions: no database and no LLM import, so the rules are unit-testable. The database enforces them (scoped views);
the planner uses them to answer 'automatically scoped' and to refuse comparisons the user is not allowed to see."""

SCOPE_COL = {"territory": "territory_name", "district": "district_name", "area": "area_name"}
SCOPE_LEVELS = {"territory": ["TERRITORY"], "district": ["TERRITORY", "DISTRICT"], "area": ["TERRITORY", "DISTRICT", "AREA"],
                "all": ["TERRITORY", "DISTRICT", "AREA", "DIVISION"]}
ROLE_SCOPE_LEVEL = {"tm": "territory", "dm": "district", "am": "area", "admin": "all"}
ROLE_PERMISSIONS = {"tm": ["sales"], "dm": ["sales"], "am": ["sales"], "admin": ["sales", "clinical", "audit"]}


def is_restricted(scope) -> bool:
    return bool(scope) and scope.get("level") != "all"


def scope_text(scope) -> str:
    if not is_restricted(scope):
        return "all territories"
    return f"{scope['level']} {scope['value']}"


def predicate_for(scope, is_geo: bool) -> str | None:
    """SQL condition that limits a relation to the user's scope (None = no restriction)."""
    if not is_restricted(scope):
        return None
    val = str(scope["value"]).replace("'", "''")
    parts = [f"{SCOPE_COL[scope['level']]} = '{val}'"]
    if is_geo:
        lv = ", ".join(f"'{x}'" for x in SCOPE_LEVELS[scope["level"]])
        parts.append(f"geo_level IN ({lv})")
    return " AND ".join(parts)


def build_access(scope, alignment_rows, hcp_names):
    """alignment_rows: (area, district, territory, division, tm_name, tm_email) tuples. Returns None for unrestricted users."""
    if not is_restricted(scope):
        return None
    lvl = scope["level"]
    idx = {"area": 0, "district": 1, "territory": 2}[lvl]
    mine = [r for r in alignment_rows if r[idx] == scope["value"]]
    return {"scope": {"level": lvl, "value": scope["value"]}, "text": scope_text(scope), "levels": SCOPE_LEVELS[lvl],
            "territories": sorted({r[2] for r in mine}),
            "districts": sorted({r[1] for r in mine}) if lvl in ("district", "area") else [],
            "areas": sorted({r[0] for r in mine}) if lvl == "area" else [],
            "tm": sorted({r[4] for r in mine} | {r[5] for r in mine}), "hcps": sorted(hcp_names)}


def check_access(ents, access, asked_level):
    """Return a refusal message if the question names something outside the user's scope or asks for a comparison above it."""
    if not access:
        return None
    t = access["text"]
    for col, val in ents:
        if col == "territory_name" and val not in access["territories"]:
            return f"{val} is outside your access scope ({t})."
        if col == "district_name" and val not in access["districts"]:
            return f"District-level data for {val} is outside your access scope ({t})."
        if col == "area_name" and val not in access["areas"]:
            return f"Area-level data for {val} is outside your access scope ({t})."
        if col in ("territory_manager_name", "territory_manager_email") and val not in access["tm"]:
            return f"That territory manager is outside your access scope ({t})."
        if col == "hcp_full_name" and val not in access["hcps"]:
            return f"That HCP is outside your access scope ({t})."
    if asked_level and asked_level != "DIVISION" and asked_level not in access["levels"]:
        plural = {"TERRITORY": "territories", "DISTRICT": "districts", "AREA": "areas"}
        allowed = ", ".join(plural[x] for x in access["levels"])
        return f"{asked_level.title()}-level comparisons are outside your access scope ({t}). You can compare: {allowed}."
    return None
