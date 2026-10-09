"""
Points the app at a throwaway provena_test_<random> MySQL database for the
whole run (docs/tasks/testing.md, sections 2-3).

provena reads its configs and touches the database at import time, so all of
this happens in pytest_configure, before any test module is collected. Don't
import provena at the top of this file.
"""

import os
import sys
import tempfile

import pytest
import sqlalchemy as sa

from tests.support.app_config import write_app_configs
from tests.support.databases import (
    DBSettings,
    clear_tables,
    create_test_database,
    database_url,
    drop_test_database,
)

_TEST_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "test_config.yaml")
_URL_ENV_VAR = "PROVENA_TEST_MYSQL_URL"

# The ProgSnap2 Metadata table is written once when the schema is created,
# and nothing writes to it afterwards, so it's kept between tests.
_KEEP_TABLES = {"metadata"}

# Set by pytest_configure.
_settings: DBSettings | None = None
_database: str | None = None
_temp_dir: tempfile.TemporaryDirectory | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _settings, _database, _temp_dir
    if "provena" in sys.modules or "provena.config.configs" in sys.modules:
        raise pytest.UsageError("provena was imported before tests/conftest.py could point it at the test database.")

    try:
        _settings = DBSettings.load(_TEST_CONFIG_PATH, url_override=os.environ.get(_URL_ENV_VAR))
    except FileNotFoundError:
        raise pytest.UsageError(
            "No test database configured. Copy tests/test_config.example.yaml to "
            f"tests/test_config.yaml and fill it in, or set {_URL_ENV_VAR}."
        )

    _database = create_test_database(_settings.mysql_server_url, _settings.database_prefix)
    _temp_dir = tempfile.TemporaryDirectory(prefix="provena_tests_")
    config_dir = os.path.join(_temp_dir.name, "config")
    url = database_url(_settings.mysql_server_url, _database)
    write_app_configs(
        config_dir,
        url.render_as_string(hide_password=False),
        root_path=os.path.join(_temp_dir.name, "data"),
    )
    os.environ["PROVENA_CONFIG_DIR"] = config_dir

    # Import the whole app now, so its import-time setup (creating the
    # tables, then reflecting them for the read side) runs once, in the
    # right order, against the test database -- not whenever the first test
    # module happens to import some piece of provena.
    try:
        import provena.main  # noqa: F401
    except BaseException:
        # pytest_unconfigure may not run after a failed configure.
        drop_test_database(_settings.mysql_server_url, _database)
        _temp_dir.cleanup()
        raise


def pytest_report_header(config: pytest.Config) -> str:
    return f"test database: {_database}"


def pytest_unconfigure(config: pytest.Config) -> None:
    if _database is None:
        return
    # Release the app's pooled connections first: one left open on the
    # server holds metadata locks that block DROP DATABASE.
    if "provena.main" in sys.modules:
        import provena.api.logging.logging
        import provena.api.read.common
        import provena.db.base
        provena.db.base.engine.dispose()
        provena.api.logging.logging.db_writer_factory.engine.dispose()
        getattr(provena.api.read.common, "__db_reader_factory").engine.dispose()

    if _settings.keep_database:
        print(f"\nKept test database {_database} (keep_database: true). Drop it by hand when done.")
    else:
        drop_test_database(_settings.mysql_server_url, _database)
    if _temp_dir is not None:
        _temp_dir.cleanup()


# --- Fixtures -----------------------------------------------------------------

@pytest.fixture(scope="session")
def db_settings() -> DBSettings:
    return _settings


@pytest.fixture(scope="session")
def test_db_engine():
    """An engine on the run's test database, separate from the app's own."""
    engine = sa.create_engine(database_url(_settings.mysql_server_url, _database))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app():
    from provena.main import app
    return app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_db(test_db_engine):
    """Empties every table after each test, so tests don't see each other's
    rows. The app's code commits internally, so per-test transactions that
    roll back aren't an option (docs/tasks/testing.md, section 2)."""
    yield
    clear_tables(test_db_engine, keep=_KEEP_TABLES)


@pytest.fixture
def scratch_database(db_settings):
    """
    Factory for extra empty databases, for tests that need a schema other
    than the app's (e.g. migrating a legacy table). Each call returns an
    engine on a new provena_test_* database; all are dropped afterwards.
    """
    created = []

    def make() -> sa.Engine:
        name = create_test_database(db_settings.mysql_server_url, db_settings.database_prefix)
        engine = sa.create_engine(database_url(db_settings.mysql_server_url, name))
        created.append((name, engine))
        return engine

    yield make
    for name, engine in created:
        engine.dispose()
        drop_test_database(db_settings.mysql_server_url, name)
