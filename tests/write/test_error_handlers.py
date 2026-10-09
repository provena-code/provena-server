"""T3: main.py's global exception handlers for errors that escape an
endpoint."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeoutError

from tests.support.provena_helpers import event, logging_error_rows, main_table_rows, student_headers, instructor_key_headers


@pytest.fixture
def lenient_client(app):
    """Returns 500 responses instead of re-raising server errors in the test."""
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def failing_events(monkeypatch):
    """Makes /events raise an unexpected exception partway through."""
    import provena.api.logging.logging as logging_module

    def boom(events):
        raise RuntimeError("boom")
    monkeypatch.setattr(logging_module, "add_codestate_ids", boom)


def test_unhandled_error_is_a_500_and_logged_as_an_error_event(lenient_client, failing_events):
    response = lenient_client.post("/events", json=[event("Session.Start")], headers=student_headers())

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert [r["EventType"] for r in main_table_rows()] == ["LoggingError"]


@pytest.mark.xfail(reason="B2: the handler sets request = None before reading the body")
def test_unhandled_error_records_the_request_body(lenient_client, failing_events):
    lenient_client.post("/events", json=[event("Session.Start", SubjectID="find-me")], headers=student_headers())
    [error] = logging_error_rows()
    assert "boom" in error["Error"]
    assert "find-me" in error["RequestBody"]


@pytest.mark.xfail(reason="B3: the handler catches MySQLdb.OperationalError, but SQLAlchemy raises its own OperationalError")
def test_database_operational_error_has_its_own_response(app, lenient_client):
    from provena.api.read.common import create_reader

    def unavailable():
        raise OperationalError("SELECT 1", {}, Exception("server has gone away"))
        yield  # pragma: no cover

    app.dependency_overrides[create_reader] = unavailable
    try:
        response = lenient_client.get("/read/subjects", headers=instructor_key_headers())
    finally:
        app.dependency_overrides.pop(create_reader)
    assert response.status_code == 500
    assert response.json() == {"detail": "Database Operational Error"}


@pytest.mark.xfail(reason="B25 (D18): an exhausted connection pool is a generic 500, not a retryable 503")
def test_pool_timeout_is_a_retryable_503(app, lenient_client):
    from provena.api.logging.logging import create_writer

    def exhausted():
        raise PoolTimeoutError("QueuePool limit of size 10 overflow 0 reached")
        yield  # pragma: no cover

    app.dependency_overrides[create_writer] = exhausted
    try:
        response = lenient_client.post("/events", json=[], headers=instructor_key_headers())
    finally:
        app.dependency_overrides.pop(create_writer)
    assert response.status_code == 503
    assert "retry-after" in response.headers
