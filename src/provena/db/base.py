import logging
logger = logging.getLogger(__name__)

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from provena.config.configs import api_config

# Hand-written SQLAlchemy tables for everything that isn't ProgSnap2 logging
# data: auth (provena.auth.models), the assignment map
# (provena.assignments.models), and future app tables. They live in the same
# physical database as the logging data (same sqlalchemy_url), but are
# defined with the normal declarative API on this Base, managed through their
# own engine/session -- not in the ProgSnap2 spec (progsnap2-provena.yaml),
# which is reserved for the ProgSnap2 format itself.
engine = create_engine(api_config.database_config.sqlalchemy_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_app_tables() -> None:
    """
    Creates any missing app tables in their current shape, then upgrades
    existing tables from older shapes (provena.db.migrations). Safe to run on
    every startup: both steps skip what's already done.
    """
    # Every module defining models on Base must be imported before
    # create_all, or its tables are silently skipped.
    import provena.assignments.models  # noqa: F401
    import provena.auth.models  # noqa: F401
    from provena.db.migrations import run_migrations

    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        run_migrations(conn)
