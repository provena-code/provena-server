import logging
logger = logging.getLogger(__name__)

from typing import Optional

from sqlalchemy import Engine, create_engine
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


def can_connect(bind: Engine) -> bool:
    """
    Whether a plain connection to the database works. Startup uses this to
    tell "the database is briefly unreachable" (tolerated: log, and keep
    serving until it's back) from "the schema couldn't be created" (fatal).
    The exception type can't tell them apart: MySQL reports e.g. a too-long
    index key (error 1071) as an OperationalError, just like a refused
    connection.
    """
    try:
        with bind.connect():
            return True
    except Exception as e:
        logger.error(f"Can't connect to the database at {bind.url.render_as_string(hide_password=True)}: {e}")
        return False


def init_app_tables(bind: Optional[Engine] = None) -> None:
    """
    Creates any missing app tables in their current shape, then upgrades
    existing tables from older shapes (provena.db.migrations). Safe to run on
    every startup: both steps skip what's already done.

    `bind` defaults to the app's engine; tests pass another one to check
    fresh/legacy schemas in a separate database.
    """
    bind = bind or engine
    # Every module defining models on Base must be imported before
    # create_all, or its tables are silently skipped.
    import provena.assignments.models  # noqa: F401
    import provena.auth.models  # noqa: F401
    from provena.db.migrations import run_migrations

    Base.metadata.create_all(bind=bind)
    with bind.begin() as conn:
        run_migrations(conn)
