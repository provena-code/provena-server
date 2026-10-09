"""
D9: startup should fail loudly when the app tables can't be created or
migrated (a DDL/programming error), but keep tolerating a database that's
briefly unreachable (a connection error), since provena.service restarts
the server anyway.

Startup runs at import time in provena.main, so these tests re-execute that
module with init_app_tables patched, then put the original module state
back.
"""

import importlib

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

import provena.db.base
import provena.main


@pytest.fixture
def rerun_main(monkeypatch):
    """Returns a function that re-imports provena.main with init_app_tables
    raising `error`. Restores provena.main afterwards."""
    saved = dict(vars(provena.main))

    def rerun(error):
        def failing_init(bind=None):
            raise error
        monkeypatch.setattr(provena.db.base, "init_app_tables", failing_init)
        importlib.reload(provena.main)

    yield rerun
    vars(provena.main).clear()
    vars(provena.main).update(saved)


def test_connection_error_at_startup_is_tolerated(rerun_main):
    rerun_main(OperationalError("CREATE TABLE ...", {}, Exception("Can't connect to MySQL server")))


@pytest.mark.xfail(reason="B26 (D9): a schema error at startup is logged and swallowed, leaving a half-created DB")
def test_schema_error_at_startup_is_fatal(rerun_main):
    with pytest.raises(ProgrammingError):
        rerun_main(ProgrammingError("CREATE TABLE ...", {}, Exception("1071 Specified key was too long")))
