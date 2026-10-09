"""
Credential and data helpers that go through the real app code. Imports
provena, so only import this from tests and fixtures (after conftest has
pointed the app at the test database), never from conftest's top level.
"""

from typing import Any

from progsnap2.database.writer.db_writer import LogResult
from sqlalchemy import select

from provena.api.logging.logging import add_codestate_ids, db_writer_factory
from provena.auth.models import User
from provena.auth.tokens import issue_token
from provena.config.configs import api_config, spec
from provena.db.base import SessionLocal

from tests.support.app_config import INSTRUCTOR_API_KEY, INSTRUCTOR_EMAIL, STUDENT_EMAIL, SUBMIT_API_KEY
from tests.support.progsnap2_events import make_event


# --- Credentials --------------------------------------------------------------

def login_headers(email: str, client_type: str = "cli") -> dict[str, str]:
    """
    Bearer headers for a real token issued to `email` (creating the user if
    needed), as if they'd logged in. Whether that email has any role is up
    to the test's auth config.
    """
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            user = User(email=email)
            db.add(user)
            db.commit()
        raw_token, _ = issue_token(db, user, client_type)
    return {"Authorization": f"Bearer {raw_token}"}


def student_headers(email: str = STUDENT_EMAIL, client_type: str = "cli") -> dict[str, str]:
    return login_headers(email, client_type)


def instructor_headers(email: str = INSTRUCTOR_EMAIL, client_type: str = "web") -> dict[str, str]:
    return login_headers(email, client_type)


def instructor_key_headers() -> dict[str, str]:
    return {"X-API-Key": INSTRUCTOR_API_KEY}


def submit_key_headers() -> dict[str, str]:
    return {"X-API-Key": SUBMIT_API_KEY}


# --- Data ---------------------------------------------------------------------

def event(event_type: str, **overrides: Any) -> dict[str, Any]:
    """An event of `event_type` under provena's spec; see make_event."""
    return make_event(spec, event_type, **overrides)


def seed_events(events: list[dict[str, Any]]) -> LogResult:
    """
    Inserts events the way /events stores them (server timestamps,
    CodeStateIDs from Code), without going through HTTP or auth. Use for
    preconditions; test /events itself through the client. Fails the test if
    the insert fails.
    """
    events = [dict(e) for e in events]
    with db_writer_factory.create_writer() as writer:
        if api_config.add_server_timestamps:
            writer.add_server_timestamps(events)
        add_codestate_ids(events)
        result = writer.add_events(events)
    assert result.success, f"seed_events failed: {result.errors}"
    return result
