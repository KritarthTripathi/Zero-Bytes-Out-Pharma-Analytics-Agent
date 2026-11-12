from app.sales.narration import plain_summary, valid_narration

TABLE = "district_name | trx\nCal2 district | 291"


def test_a_summary_may_only_use_numbers_in_the_result():
    assert valid_narration("Cal2 district has the most TRx for flend with 291.", TABLE)
    assert not valid_narration("Cal2 district has the most TRx for flend with 999.", TABLE)


def test_a_code_that_is_not_in_the_result_is_rejected():
    assert not valid_narration("Cal2 district has <HCP_FULL_NAME_3> more TRx for flend with 291.", TABLE)
    assert valid_narration("The top prescriber is <HCP_FULL_NAME_3> with 291 TRx.", "hcp_full_name | trx\n<HCP_FULL_NAME_3> | 291")


def test_too_short_summaries_fall_back():
    assert not valid_narration("1 | 67", "territory_name | trx\ncal_terr_1 | 67")
    assert plain_summary(["territory_name", "trx"], [["cal_terr_1", 67]]) == "territory_name = cal_terr_1; trx = 67"
    assert plain_summary(["a"], [[1], [2]]) == "2 rows returned (see the table)."
