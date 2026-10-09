"""
T3: operations that commit in more than one step, with the second step
forced to fail, to pin what's left in the database. The app's helpers
commit internally (docs/tasks/testing.md, section 2), so one request isn't
one transaction.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from progsnap2.database.writer.sql_writer import SQLWriter

from provena.api.logging.logging import add_error_event
from provena.auth.models import OAuthIdentity, Token, User
from provena.db.base import SessionLocal

from tests.support.app_config import STUDENT_EMAIL
from tests.support.fake_auth import identity, redirect_params
from tests.support.provena_helpers import logging_error_rows, main_table_rows


def _fail(*args, **kwargs):
    raise RuntimeError("forced failure")


# --- _add_error_event: LoggingError event, then LinkLoggingError row ---------

def test_error_event_without_its_details(monkeypatch):
    # [inferred] OK as is: the event still records that *something* failed,
    # and the result reports the missing details.
    monkeypatch.setattr(SQLWriter, "add_link_table_entry", _fail)

    result = add_error_event("the error", "the body")

    assert result.success is False
    assert any("Could not log error message in link table" in e for e in result.errors)
    assert [r["EventType"] for r in main_table_rows()] == ["LoggingError"]
    assert logging_error_rows() == []


def test_error_details_without_their_event(monkeypatch):
    # [inferred] The reverse: the details row is still written, keyed by a
    # LoggingErrorID that no event refers to, and its Error says the event
    # itself couldn't be saved. Findable, so OK as is.
    monkeypatch.setattr(SQLWriter, "add_events", _fail)

    result = add_error_event("the error", "the body")

    assert result.success is False
    assert main_table_rows() == []
    [error] = logging_error_rows()
    assert error["Error"].startswith("Could not log error event: the error")
    assert error["RequestBody"] == "the body"


# --- google_callback: user + identity, then the token ------------------------

@pytest.fixture
def lenient_https_client(app):
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield client


def _counts():
    with SessionLocal() as db:
        return {model.__tablename__: db.execute(select(func.count()).select_from(model)).scalar_one()
                for model in (User, OAuthIdentity, Token)}


def _log_in(client, backend, who):
    client.get("/auth/login", params={"client_redirect_uri": "http://127.0.0.1:5000/cb", "client_type": "cli"},
               follow_redirects=False)
    backend.identity = who
    return client.get("/auth/google/callback", follow_redirects=False)


def test_failed_token_issue_leaves_the_user_and_a_retry_works(lenient_https_client, fake_backend, monkeypatch):
    import provena.api.auth.auth as auth_module
    real_issue_token = auth_module.issue_token
    monkeypatch.setattr(auth_module, "issue_token", _fail)

    response = _log_in(lenient_https_client, fake_backend, identity(STUDENT_EMAIL, "sub-1"))

    # The user and identity were committed before the token step failed.
    assert response.status_code == 500
    assert _counts() == {"auth_users": 1, "auth_oauth_identities": 1, "auth_tokens": 0}

    # Harmless: logging in again reuses them rather than duplicating.
    monkeypatch.setattr(auth_module, "issue_token", real_issue_token)
    response = _log_in(lenient_https_client, fake_backend, identity(STUDENT_EMAIL, "sub-1"))
    assert response.status_code == 307
    assert "token" in redirect_params(response.headers["location"])
    assert _counts() == {"auth_users": 1, "auth_oauth_identities": 1, "auth_tokens": 1}
