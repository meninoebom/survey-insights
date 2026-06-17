"""Engine, session factory, and table creation.

`init_db` is the create-tables hook the app lifespan calls on boot (wired with
the app in the Docker milestone). It is idempotent, so booting twice is safe.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from survey.config import get_settings
from survey.db.models import Base


def create_db_engine(url: str | None = None) -> Engine:
    """Create an engine from the given URL, or from the configured DATABASE_URL."""
    resolved = url if url is not None else get_settings().database_url
    return create_engine(resolved, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to the engine."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all tables if they are absent. Idempotent; safe on every boot."""
    Base.metadata.create_all(engine)
