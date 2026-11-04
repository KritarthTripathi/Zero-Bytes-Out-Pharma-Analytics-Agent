import pytest

from app.sales.sql_guard import guard


@pytest.mark.parametrize("sql", [
    "SELECT SUM(trx) FROM base.v_hcp_rx_latest WHERE grain_code = '13W'",
    "SELECT SUM(trx) FROM main.v_hcp_rx_latest WHERE grain_code = '13W'",
    "SELECT * FROM base.main.hcp_summary_with_territory_and_tm_info",
])
def test_schema_qualified_tables_are_rejected(sql):
    with pytest.raises(ValueError):
        guard(sql, "sales_hcp")
