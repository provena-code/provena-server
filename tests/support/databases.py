"""
Throwaway MySQL databases for tests. Nothing here imports provena, so it can
be used before the app's configs exist (see tests/conftest.py), and could
move to the toolbox later (docs/tasks/testing.md, section 4).
"""

import secrets
from dataclasses import dataclass
from typing import Iterable

import sqlalchemy as sa
import yaml
from sqlalchemy.engine import URL, Engine, make_url

# Every database the suite creates, and the only ones it will clear or drop.
REQUIRED_PREFIX = "provena_test_"


@dataclass
class DBSettings:
    mysql_server_url: str
    database_prefix: str = REQUIRED_PREFIX
    keep_database: bool = False

    @classmethod
    def load(cls, path: str, url_override: str | None = None) -> "DBSettings":
        data = {}
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = yaml.safe_load(file) or {}
        except FileNotFoundError:
            if not url_override:
                raise
        if url_override:
            data["mysql_server_url"] = url_override
        settings = cls(**data)
        if not settings.database_prefix.startswith(REQUIRED_PREFIX):
            raise ValueError(f"database_prefix must start with {REQUIRED_PREFIX!r}, got {settings.database_prefix!r}")
        return settings


def database_url(server_url: str, database: str) -> URL:
    url = make_url(server_url)
    if url.database:
        raise ValueError(f"mysql_server_url must not name a database: {url.render_as_string(hide_password=True)}")
    return url.set(database=database)


def check_test_database(name: str) -> None:
    if not name or not name.startswith(REQUIRED_PREFIX):
        raise RuntimeError(f"Refusing to touch database {name!r}: test databases must start with {REQUIRED_PREFIX!r}")


def create_test_database(server_url: str, prefix: str = REQUIRED_PREFIX) -> str:
    """Creates an empty, uniquely named database and returns its name."""
    name = prefix + secrets.token_hex(4)
    check_test_database(name)
    _execute_on_server(server_url, f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
    return name


def drop_test_database(server_url: str, name: str) -> None:
    check_test_database(name)
    _execute_on_server(server_url, f"DROP DATABASE IF EXISTS `{name}`")


def _execute_on_server(server_url: str, statement: str) -> None:
    # Disposed right away: a pooled connection left open on the server would
    # hold metadata locks and can block a later DROP DATABASE.
    engine = sa.create_engine(server_url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(statement))
    finally:
        engine.dispose()


def clear_tables(engine: Engine, keep: Iterable[str] = ()) -> None:
    """
    Deletes every row from every table, except tables named in `keep`
    (case-insensitive). DELETE rather than TRUNCATE, which is DDL in MySQL and
    slower on small tables; foreign key checks are off for the duration so
    the order doesn't matter.
    """
    check_test_database(engine.url.database)
    keep = {name.lower() for name in keep}
    with engine.begin() as conn:
        tables = [name for name in sa.inspect(conn).get_table_names() if name.lower() not in keep]
        conn.execute(sa.text("SET FOREIGN_KEY_CHECKS = 0"))
        try:
            for table in tables:
                conn.execute(sa.text(f"DELETE FROM `{table}`"))
        finally:
            conn.execute(sa.text("SET FOREIGN_KEY_CHECKS = 1"))


def show_create_table(engine: Engine, table: str) -> str:
    with engine.connect() as conn:
        return conn.execute(sa.text(f"SHOW CREATE TABLE `{table}`")).one()[1]
