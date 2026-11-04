from app.common.access import build_access, check_access, predicate_for, scope_text

ROWS = [("California Area", "Cal1 district", "cal_terr_1", "CMH", "<TERRITORY_MANAGER_NAME_1>", "<TERRITORY_MANAGER_EMAIL_1>"),
        ("California Area", "Cal1 district", "cal_terr_2", "GI", "<TERRITORY_MANAGER_NAME_2>", "<TERRITORY_MANAGER_EMAIL_2>"),
        ("California Area", "Cal2 district", "cal_terr_4", "OBU", "<TERRITORY_MANAGER_NAME_4>", "<TERRITORY_MANAGER_EMAIL_4>"),
        ("New York Area", "Ny1 district", "ny_terr_1", "CMH", "<TERRITORY_MANAGER_NAME_9>", "<TERRITORY_MANAGER_EMAIL_9>")]
TM = {"level": "territory", "value": "cal_terr_2"}
DM = {"level": "district", "value": "Cal1 district"}
AM = {"level": "area", "value": "California Area"}


def test_unrestricted_user_has_no_access_context():
    assert build_access({"level": "all", "value": None}, ROWS, []) is None
    assert predicate_for({"level": "all", "value": None}, True) is None


def test_predicates():
    assert predicate_for(TM, False) == "territory_name = 'cal_terr_2'"
    assert predicate_for(DM, True) == "district_name = 'Cal1 district' AND geo_level IN ('TERRITORY', 'DISTRICT')"
    assert predicate_for(AM, True) == "area_name = 'California Area' AND geo_level IN ('TERRITORY', 'DISTRICT', 'AREA')"
    assert predicate_for({"level": "district", "value": "O'Hare"}, False) == "district_name = 'O''Hare'"


def test_access_contexts():
    tm, dm, am = (build_access(s, ROWS, ["<HCP_FULL_NAME_1>"]) for s in (TM, DM, AM))
    assert tm["territories"] == ["cal_terr_2"] and tm["districts"] == [] and tm["areas"] == []
    assert dm["territories"] == ["cal_terr_1", "cal_terr_2"] and dm["districts"] == ["Cal1 district"] and dm["areas"] == []
    assert len(am["territories"]) == 3 and am["districts"] == ["Cal1 district", "Cal2 district"] and am["areas"] == ["California Area"]
    assert "<TERRITORY_MANAGER_NAME_1>" in dm["tm"] and "<TERRITORY_MANAGER_NAME_4>" not in dm["tm"]


def test_entities_outside_scope_are_refused():
    tm, dm, am = (build_access(s, ROWS, ["<HCP_FULL_NAME_1>"]) for s in (TM, DM, AM))
    assert check_access([("territory_name", "cal_terr_2")], tm, None) is None
    assert "outside your access scope" in check_access([("territory_name", "cal_terr_1")], tm, None)
    assert check_access([("territory_name", "cal_terr_1")], dm, None) is None
    assert check_access([("territory_name", "ny_terr_1")], am, None)
    assert check_access([("district_name", "Cal1 district")], tm, None)           # a TM cannot read district totals
    assert check_access([("area_name", "California Area")], dm, None)             # a DM cannot read area totals
    assert check_access([("area_name", "California Area")], am, None) is None
    assert check_access([("territory_manager_name", "<TERRITORY_MANAGER_NAME_9>")], am, None)
    assert check_access([("territory_manager_name", "<TERRITORY_MANAGER_NAME_2>")], dm, None) is None
    assert check_access([("hcp_full_name", "<HCP_FULL_NAME_99>")], tm, None)
    assert check_access([("product_name", "menji"), ("division_name", "Neuro")], tm, None) is None


def test_comparison_levels():
    tm, dm, am = (build_access(s, ROWS, []) for s in (TM, DM, AM))
    assert "Area-level" in check_access([], tm, "AREA") and "Area-level" in check_access([], dm, "AREA")
    assert check_access([], dm, "DISTRICT") is None and check_access([], am, "AREA") is None
    assert check_access([], tm, "DISTRICT") and check_access([], tm, "TERRITORY") is None
    assert check_access([], tm, "DIVISION") is None                                 # answered inside the user's scope
    assert scope_text(DM) == "district Cal1 district"
