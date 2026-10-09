"""
T5: overlapping requests against a live server. These assert invariants
that must hold under any interleaving (no lost rows, no duplicates, no
deadlocks).
"""

import asyncio
import time
from collections import Counter

import httpx
import pytest
from sqlalchemy import func, select

from provena.assignments.models import AssignmentMapping
from provena.auth.models import User
from provena.db.base import SessionLocal

from tests.support.app_config import STUDENT_EMAIL
from tests.support.fake_auth import identity
from tests.support.provena_helpers import (
    event, instructor_key_headers, main_table_rows, seed_events, student_headers,
)

pytestmark = pytest.mark.concurrency


def run_concurrently(base_url, requests, timeout=30):
    """Sends all `requests` ((method, path, kwargs) tuples) at once and
    returns the responses in the same order."""
    async def main():
        limits = httpx.Limits(max_connections=len(requests))
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout, limits=limits) as client:
            return await asyncio.gather(*(client.request(method, path, **kwargs) for method, path, kwargs in requests))
    return asyncio.run(main())


def test_overlapping_batches_lose_no_events(live_server):
    headers = student_headers()
    sessions = [f"sess-{n}" for n in range(4)]
    batches = [
        [event("File.Edit", SessionID=sessions[b % 4], Order=b * 100 + i, EditType="Insert") for i in range(25)]
        for b in range(20)
    ]

    responses = run_concurrently(live_server, [("POST", "/events", {"json": batch, "headers": headers}) for batch in batches])

    assert [r.status_code for r in responses] == [200] * 20
    assert all(r.json()["success"] for r in responses)
    rows = main_table_rows()
    assert len(rows) == 20 * 25
    assert len({r["EventID"] for r in rows}) == 20 * 25
    with httpx.Client(base_url=live_server) as client:
        for n, session in enumerate(sessions):
            expected = max(b * 100 + 24 for b in range(20) if b % 4 == n)
            assert client.get(f"/read/sessions/{session}/last_synced_order").json() == expected


def test_concurrent_mapping_updates_neither_fail_nor_duplicate(live_server):
    subjects = [f"s{n}" for n in range(20)]
    seed_events(
        [event("File.Save", SubjectID=s, CodeStateSection="proj/main.py", Code=f"code {s}") for s in subjects]
        + [event("Submit", SubjectID=s, AssignmentID="A1", CodeStateSection="main.py", Code=f"code {s}",
                 ServerTimestamp="2026-02-01T10:00:00") for s in subjects]
    )

    responses = run_concurrently(live_server, [
        ("POST", "/read/update_mapping_table", {"headers": instructor_key_headers()}) for _ in range(10)
    ])

    assert Counter(r.status_code for r in responses) == {200: 10}
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(AssignmentMapping)).scalar_one() == len(subjects)


# --- First-login race -----------------------------------------------------------

def _start_login(base_url) -> str:
    """Runs /auth/login and returns the session cookie. (The cookie is
    Secure, so httpx won't send it back over plain http by itself.)"""
    response = httpx.get(f"{base_url}/auth/login", params={"client_redirect_uri": "http://127.0.0.1:5000/cb", "client_type": "cli"})
    return response.cookies["session"]


def test_simultaneous_first_logins_create_one_user(live_server, fake_backend):
    """
    Two first logins for the same new user at once. The lookup-then-insert
    in google_callback would race on auth_users.email, but it can't today:
    the handler is `async def` with blocking DB calls and no await after the
    backend callback, so a single uvicorn worker (as in production) runs
    them one after the other. That changes with more workers, or if the
    handler becomes sync (threadpool) or awaits mid-way (D17, B18).
    """
    cookies = [_start_login(live_server) for _ in range(5)]
    fake_backend.identity = identity(STUDENT_EMAIL, "sub-1")

    responses = run_concurrently(live_server, [
        ("GET", "/auth/google/callback", {"headers": {"Cookie": f"session={cookie}"}}) for cookie in cookies
    ])

    assert [r.status_code for r in responses] == [307] * 5
    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(User)).scalar_one() == 1


# --- Connection pool exhaustion -------------------------------------------------

@pytest.fixture
def slow_writer(app):
    """Each /events request holds its pooled connection for HOLD seconds."""
    from provena.api.logging.logging import create_writer, db_writer_factory

    def slow_create_writer():
        with db_writer_factory.create_writer() as writer:
            writer.session.connection()  # check out a pooled connection now
            time.sleep(slow_create_writer.hold)
            yield writer

    slow_create_writer.hold = 3.0
    app.dependency_overrides[create_writer] = slow_create_writer
    yield slow_create_writer
    app.dependency_overrides.pop(create_writer)


@pytest.mark.slow
def test_pool_exhaustion_fails_fast_without_deadlock(live_server, slow_writer):
    """The write pool is pool_size 10, max_overflow 0, pool_timeout 2s. Twelve
    requests that each hold a connection for 3s: ten get one, two time out."""
    headers = instructor_key_headers()  # no DB lookup for the credential
    started = time.monotonic()

    responses = run_concurrently(live_server, [("POST", "/events", {"json": [], "headers": headers}) for _ in range(12)])

    elapsed = time.monotonic() - started
    # The two that time out fail; D18 wants a 503 + Retry-After there, which
    # tests/write/test_error_handlers.py checks without the wait (B25).
    assert Counter(r.status_code for r in responses)[200] == 10
    assert sorted(r.status_code for r in responses)[10:] in ([500, 500], [503, 503])
    # Bounded by hold + timeouts: the error handler's own attempt to log
    # the failure waits for a connection too, but doesn't deadlock.
    assert elapsed < 10
