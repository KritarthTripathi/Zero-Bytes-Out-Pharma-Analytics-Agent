from app.common.qmask import safe_reidentify

MAP = {"<HCP_FULL_NAME_1>": "Alice Morales", "<HCP_FULL_NAME_3>": "Gabriel Jensen"}


def test_only_codes_in_the_result_are_replaced():
    allowed = {"<HCP_FULL_NAME_1>"}
    out = safe_reidentify("<HCP_FULL_NAME_1> and <HCP_FULL_NAME_3>", MAP, allowed)
    assert out == "Alice Morales and <HCP_FULL_NAME_3>"
    assert safe_reidentify("no codes here", MAP, allowed) == "no codes here"
