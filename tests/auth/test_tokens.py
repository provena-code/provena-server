"""T2: token issue/resolve/revoke and expiry policy (provena.auth.tokens)."""

import datetime as dt
import hashlib

import pytest
from sqlalchemy import select

from provena.auth import tokens
from provena.auth.models import Token, User
from provena.db.base import SessionLocal

NOW = dt.datetime(2026, 3, 1, 12, 0, 0)


@pytest.fixture
def clock(monkeypatch):
    """Controls provena.auth.tokens' notion of "now"."""
    class Clock:
        now = NOW

        def advance(self, **delta):
            self.now += dt.timedelta(**delta)

    clock = Clock()
    monkeypatch.setattr(tokens, "_utcnow", lambda: clock.now)
    return clock


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def user(db):
    user = User(email="t@student.test")
    db.add(user)
    db.commit()
    return user


def test_only_the_hash_is_stored(db, user, clock):
    raw, token = tokens.issue_token(db, user, tokens.CLI)

    stored = db.execute(select(Token.token_hash)).scalars().all()
    assert stored == [hashlib.sha256(raw.encode()).hexdigest()]
    assert raw not in stored


def test_tokens_are_unique(db, user, clock):
    raws = {tokens.issue_token(db, user, tokens.CLI)[0] for _ in range(5)}
    assert len(raws) == 5


@pytest.mark.parametrize("client_type, ttl", [
    (tokens.CLI, dt.timedelta(days=90)),
    (tokens.WEB, dt.timedelta(hours=12)),
    ("something-else", dt.timedelta(hours=12)),  # [inferred] unknown types get the web policy
])
def test_expiry_by_client_type(db, user, clock, client_type, ttl):
    _, token = tokens.issue_token(db, user, client_type)
    assert token.expires_at == NOW + ttl


def test_resolve_returns_the_token_and_its_user(db, user, clock):
    raw, _ = tokens.issue_token(db, user, tokens.WEB)
    resolved = tokens.resolve_token(db, raw)
    assert resolved is not None
    assert resolved.user.email == "t@student.test"


def test_unknown_token_resolves_to_none(db, clock):
    assert tokens.resolve_token(db, "not-a-token") is None


def test_cli_expiry_slides_on_use(db, user, clock):
    raw, _ = tokens.issue_token(db, user, tokens.CLI)
    clock.advance(days=60)

    resolved = tokens.resolve_token(db, raw)

    assert resolved.last_used_at == clock.now
    assert resolved.expires_at == clock.now + dt.timedelta(days=90)


def test_web_expiry_does_not_slide(db, user, clock):
    raw, _ = tokens.issue_token(db, user, tokens.WEB)
    clock.advance(hours=6)

    resolved = tokens.resolve_token(db, raw)

    assert resolved.last_used_at == clock.now
    assert resolved.expires_at == NOW + dt.timedelta(hours=12)


def test_token_is_still_valid_at_exactly_expires_at(db, user, clock):
    # [inferred] Expired means strictly after expires_at.
    raw, _ = tokens.issue_token(db, user, tokens.WEB)
    clock.advance(hours=12)
    assert tokens.resolve_token(db, raw) is not None


def test_expired_token_resolves_to_none(db, user, clock):
    raw, _ = tokens.issue_token(db, user, tokens.WEB)
    clock.advance(hours=12, microseconds=1)
    assert tokens.resolve_token(db, raw) is None


def test_idle_cli_token_expires(db, user, clock):
    raw, _ = tokens.issue_token(db, user, tokens.CLI)
    clock.advance(days=91)
    assert tokens.resolve_token(db, raw) is None


def test_expired_tokens_are_not_deleted(db, user, clock):
    # Pinned: expired rows stay in auth_tokens (no cleanup job yet).
    raw, _ = tokens.issue_token(db, user, tokens.WEB)
    clock.advance(days=1)
    tokens.resolve_token(db, raw)
    assert db.execute(select(Token)).scalars().all()


def test_revoke_deletes_only_that_token(db, user, clock):
    raw_a, token_a = tokens.issue_token(db, user, tokens.CLI)
    raw_b, _ = tokens.issue_token(db, user, tokens.CLI)

    tokens.revoke_token(db, token_a)

    assert tokens.resolve_token(db, raw_a) is None
    assert tokens.resolve_token(db, raw_b) is not None
