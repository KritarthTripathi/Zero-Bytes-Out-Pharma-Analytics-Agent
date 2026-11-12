from typing import TypedDict


class SalesState(TypedDict, total=False):
    question: str            # already masked
    agent: str               # sales_geo | sales_hcp | sales_activity | sales_all
    skip_narrate: bool
    plan: dict               # resolved facts + query template (visible in Phoenix, masked)
    views: list
    data_end: str
    sql_raw: str
    sql: str
    columns: list
    rows: list
    table: str
    answer: str
    error: str
    attempts: int
