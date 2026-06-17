from sqlalchemy import Engine, inspect

from survey.db.session import init_db


def test_init_db_creates_tables_and_read_index(clean_engine: Engine) -> None:
    init_db(clean_engine)
    inspector = inspect(clean_engine)

    assert {"responses", "distributions"} <= set(inspector.get_table_names())

    # zip_code must be text to preserve leading zeros.
    response_columns = {
        c["name"]: str(c["type"]).upper() for c in inspector.get_columns("responses")
    }
    assert "zip_code" in response_columns
    assert "CHAR" in response_columns["zip_code"] or "TEXT" in response_columns["zip_code"]

    # The named read index on (measure, dimension, group_value).
    index_columns = [ix["column_names"] for ix in inspector.get_indexes("distributions")]
    assert ["measure", "dimension", "group_value"] in index_columns
