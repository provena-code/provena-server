"""
Checks that the test infrastructure itself works: the app is pointed at the
throwaway database, the helpers produce data the app accepts, and tests are
isolated from each other.
"""

import sqlalchemy as sa

from progsnap2.spec.enums import CoreTables

from tests.support.databases import REQUIRED_PREFIX, clear_tables
from tests.support.provena_helpers import event, instructor_key_headers, seed_events, student_headers


def test_app_registers_routers(app):
    paths = {route.path for route in app.routes}
    assert {"/events", "/submit", "/read/assignments", "/auth/login"} <= paths


def test_app_uses_the_test_database(test_db_engine):
    import provena.api.read.common
    from provena.api.logging.logging import db_writer_factory
    from provena.db.base import engine as app_tables_engine

    reader_factory = getattr(provena.api.read.common, "__db_reader_factory")
    in_use = {
        "write": db_writer_factory.engine.url.database,
        "read": reader_factory.engine.url.database,
        "app tables": app_tables_engine.url.database,
    }
    assert set(in_use.values()) == {test_db_engine.url.database}, in_use
    assert test_db_engine.url.database.startswith(REQUIRED_PREFIX)


def test_last_synced_order_is_minus_one_on_empty_db(client):
    response = client.get("/read/sessions/no-such-session/last_synced_order")
    assert response.status_code == 200
    assert response.json() == -1


def test_posted_events_are_readable(client):
    events = [event("File.Edit", SessionID="s-post", Order=order) for order in (1, 2, 3)]

    response = client.post("/events", json=events, headers=student_headers())

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert client.get("/read/sessions/s-post/last_synced_order").json() == 3


def test_seeded_events_are_readable(client):
    seed_events([event("Session.Start", SessionID="s-seed", Order=7)])
    assert client.get("/read/sessions/s-seed/last_synced_order").json() == 7


def test_instructor_key_reaches_read_endpoints(client):
    seed_events([event("File.Edit", AssignmentID="A1")])
    response = client.get("/read/assignments", headers=instructor_key_headers())
    assert response.status_code == 200
    assert "A1" in response.json()


def test_clear_tables_empties_everything_but_metadata(test_db_engine):
    seed_events([event("File.Edit")])
    student_headers()  # creates a user and a token

    clear_tables(test_db_engine, keep={"metadata"})

    with test_db_engine.connect() as conn:
        counts = {
            table: conn.execute(sa.text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one()
            for table in sa.inspect(conn).get_table_names()
        }
    metadata_table = next(name for name in counts if name.lower() == CoreTables.Metadata.lower())
    assert counts.pop(metadata_table) > 0
    assert all(count == 0 for count in counts.values()), counts
