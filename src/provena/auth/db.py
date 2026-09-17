from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from provena.configs import api_config

# Auth tables are hand-written SQLAlchemy models (see models.py), not
# generated from the ProgSnap2 spec like the logging tables. They live in the
# same physical database as the logging data (same sqlalchemy_url), but are
# managed through their own engine/session and their own declarative Base.
engine = create_engine(api_config.database_config.sqlalchemy_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
