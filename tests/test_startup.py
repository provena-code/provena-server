"""
D9: startup fails loudly when the schema can't be created or migrated, but
tolerates a database that's briefly unreachable (provena.service restarts
the server anyway). Both startup steps are covered: the ProgSnap2 tables
(provena.api.logging.logging) and the app tables (provena.main).

Startup runs at import time, so these tests re-execute a module with parts
of it patched, then put the original module state back.
"""

import importlib

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

import provena.api.logging.logging
import provena.db.base
import provena.main
from progsnap2.database.writer.sql_writer import SQLWriter

# MySQL reports both of these as OperationalError-or-similar; only the
# connection probe tells a schema error from an unreachable server.
SCHEMA_ERRORS = [
    ProgrammingError("CREATE TABLE ...", {}, Exception("1064 You have an error in your SQL syntax")),
    OperationalError("CREATE TABLE ...", {}, Exception("1071 Specified key was too long")),
]
SCHEMA_ERROR_IDS = ["ProgrammingError", "OperationalError-1071"]


@pytest.fixture
def rerun(monkeypatch):
    """Returns a function that re-executes a module. Restores every
    re-executed module's original state afterwards."""
    saved = {}

    def rerun(module):
        saved.setdefault(module, dict(vars(module)))
        importlib.reload(module)

    yield rerun
    for module, state in saved.items():
        new_factory = vars(module).get("db_writer_factory")
        if new_factory is not None and new_factory is not state.get("db_writer_factory"):
            new_factory.engine.dispose()
        vars(module).clear()
        vars(module).update(state)


def _unreachable(monkeypatch):
    monkeypatch.setattr(provena.db.base, "can_connect", lambda bind: False)


def _raise(error):
    def fail(*args, **kwargs):
        raise error
    return fail


# --- App tables (provena.main) -----------------------------------------------

def test_unreachable_database_skips_app_tables(rerun, monkeypatch):
    _unreachable(monkeypatch)
    monkeypatch.setattr(provena.db.base, "init_app_tables", _raise(AssertionError("shouldn't be called")))
    rerun(provena.main)


@pytest.mark.parametrize("error", SCHEMA_ERRORS, ids=SCHEMA_ERROR_IDS)
def test_app_table_schema_error_is_fatal(rerun, monkeypatch, error):
    monkeypatch.setattr(provena.db.base, "init_app_tables", _raise(error))
    with pytest.raises(type(error)):
        rerun(provena.main)


# --- ProgSnap2 tables (provena.api.logging.logging) ---------------------------

def test_unreachable_database_skips_progsnap2_tables(rerun, monkeypatch):
    _unreachable(monkeypatch)
    monkeypatch.setattr(SQLWriter, "initialize_database", _raise(AssertionError("shouldn't be called")))
    rerun(provena.api.logging.logging)


@pytest.mark.parametrize("error", SCHEMA_ERRORS, ids=SCHEMA_ERROR_IDS)
def test_progsnap2_schema_error_is_fatal(rerun, monkeypatch, error):
    monkeypatch.setattr(SQLWriter, "initialize_database", _raise(error))
    with pytest.raises(type(error)):
        rerun(provena.api.logging.logging)


def test_can_connect_reports_an_unreachable_server():
    from sqlalchemy import create_engine
    engine = create_engine("mysql://nobody:nothing@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    try:
        assert provena.db.base.can_connect(engine) is False
    finally:
        engine.dispose()
