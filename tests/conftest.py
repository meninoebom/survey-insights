import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine

from survey.db.models import Base


@pytest.fixture
def clean_engine() -> Iterator[Engine]:
    """A connected engine with an empty schema, or skip if no Postgres is set.

    Integration tests need a disposable Postgres (via DATABASE_URL). Pure unit
    tests do not use this fixture and run regardless.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set; integration test needs a Postgres")
    engine = create_engine(url, future=True)
    Base.metadata.drop_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
