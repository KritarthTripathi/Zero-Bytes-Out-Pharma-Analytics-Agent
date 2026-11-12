from app.common.access import build_access
from app.sales.semantic.catalog import make_plan

K = {"territory_name": ["cal_terr_1", "cal_terr_2", "cal_terr_4", "ny_terr_1"], "district_name": ["Cal1 district", "Cal2 district", "Ny1 district"],
     "area_name": ["California Area", "New York Area"], "division_name": ["Neuro", "GI"], "product_name": ["menji", "flend"], "address_line": []}
ROWS = [("California Area", "Cal1 district", "cal_terr_1", "CMH", "<TERRITORY_MANAGER_NAME_1>", "<TERRITORY_MANAGER_EMAIL_1>"),
        ("California Area", "Cal1 district", "cal_terr_2", "GI", "<TERRITORY_MANAGER_NAME_2>", "<TERRITORY_MANAGER_EMAIL_2>"),
        ("California Area", "Cal2 district", "cal_terr_4", "OBU", "<TERRITORY_MANAGER_NAME_4>", "<TERRITORY_MANAGER_EMAIL_4>"),
        ("New York Area", "Ny1 district", "ny_terr_1", "CMH", "<TERRITORY_MANAGER_NAME_9>", "<TERRITORY_MANAGER_EMAIL_9>")]
TM = build_access({"level": "territory", "value": "cal_terr_2"}, ROWS, [])
DM = build_access({"level": "district", "value": "Cal1 district"}, ROWS, [])
AM = build_access({"level": "area", "value": "California Area"}, ROWS, [])


def test_a_tm_cannot_compare_above_territory_level():
    p = make_plan("Which area has the most TRx for menji?", K, "sales_geo", TM)
    assert p["denied"] and p["out_of_scope"] and "outside your access scope" in p["message"]


def test_a_dm_can_rank_territories_but_not_areas():
    assert not make_plan("Which territory has the most TRx for flend?", K, "sales_geo", DM)["out_of_scope"]
    assert make_plan("Which area has the most TRx for flend?", K, "sales_geo", DM)["denied"]
    assert not make_plan("Which district has the most TRx for flend?", K, "sales_geo", AM)["out_of_scope"]


def test_division_totals_are_answered_inside_the_scope():
    t = make_plan("Which division has the most TRx in the latest week?", K, "sales_geo", DM)["template"]
    assert "geo_level = 'TERRITORY'" in t and "GROUP BY division_name" in t and "SELECT division_name" in t
    t0 = make_plan("Which division has the most TRx in the latest week?", K, "sales_geo", None)["template"]
    assert "geo_level = 'DIVISION'" in t0                                            # unrestricted behaviour is unchanged


def test_named_places_outside_the_scope_are_refused():
    assert make_plan("What is the TRx of cal_terr_4 in the latest month?", K, "sales_geo", DM)["denied"]
    assert not make_plan("What is the TRx of cal_terr_4 in the latest month?", K, "sales_geo", AM)["out_of_scope"]
    assert make_plan("Top 5 prescribers in Ny1 district", K, "sales_hcp", AM)["denied"]


def test_no_entity_means_the_user_scope():
    t = make_plan("What is the TRx in the latest month?", K, "sales_geo", DM)["template"]
    assert "geo_level = 'TERRITORY'" in t and "SUM(trx)" in t and "GROUP BY" not in t     # summed over the user's own territories
    assert make_plan("What is the TRx in the latest month?", K, "sales_geo", DM)["scope_note"] == "district Cal1 district"


def test_territory_manager_lookups_respect_scope():
    q = "Top 5 prescribers for the territory managed by <TERRITORY_MANAGER_NAME_9>"
    assert make_plan(q, K, "sales_hcp", AM)["denied"]
    assert not make_plan("Top 5 prescribers for the territory managed by <TERRITORY_MANAGER_NAME_1>", K, "sales_hcp", DM)["out_of_scope"]
