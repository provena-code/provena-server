import datetime as dt
import hashlib
import secrets
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from provena.auth.models import Token, User
from provena.configs import auth_config

CLI = "cli"
WEB = "web"


def _utcnow() -> dt.datetime:
    # Naive UTC, to match how Token's DateTime columns are actually stored
    # and read back -- see the same helper in provena.auth.models.
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _ttl_for(client_type: str) -> dt.timedelta:
    if client_type == CLI:
        return dt.timedelta(days=auth_config.token.cli_ttl_days)
    return dt.timedelta(hours=auth_config.token.web_ttl_hours)


def issue_token(db: Session, user: User, client_type: str) -> Tuple[str, Token]:
    """
    Creates and persists a new token for `user`, returning the raw (unhashed)
    value alongside the Token row (e.g. for its expires_at). The raw value is
    only ever available here; only its hash is stored.
    """
    raw_token = secrets.token_urlsafe(32)
    now = _utcnow()
    token = Token(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        client_type=client_type,
        created_at=now,
        last_used_at=now,
        expires_at=now + _ttl_for(client_type),
    )
    db.add(token)
    db.commit()
    return raw_token, token


def resolve_token(db: Session, raw_token: str) -> Optional[Token]:
    """
    Looks up a token by its raw value. Returns None if it doesn't exist or has
    expired. "cli" tokens have a sliding expiry, so a successful lookup here
    also pushes out their expires_at.
    """
    token = db.execute(
        select(Token).where(Token.token_hash == _hash_token(raw_token))
    ).scalar_one_or_none()
    if token is None:
        return None

    now = _utcnow()
    if token.expires_at < now:
        return None

    token.last_used_at = now
    if token.client_type == CLI:
        token.expires_at = now + _ttl_for(CLI)
    db.commit()
    return token


def revoke_token(db: Session, token: Token) -> None:
    db.delete(token)
    db.commit()
