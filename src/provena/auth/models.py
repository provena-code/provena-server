import datetime as dt
import uuid
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from provena.auth.db import Base


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> dt.datetime:
    # Naive UTC, not timezone-aware: neither SQLite nor MySQL actually
    # preserves tzinfo through a round trip (SQLAlchemy's DateTime(timezone=True)
    # is a no-op on both), so storing aware values just risks later
    # comparisons between an aware "now" and a naive value read back from the
    # DB. Consistently-naive-UTC sidesteps that across both backends.
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "auth_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=_utcnow)

    identities: Mapped[List["OAuthIdentity"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    tokens: Mapped[List["Token"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class OAuthIdentity(Base):
    """Links a User to a single identity from an OAuth provider (e.g. one Google account)."""

    __tablename__ = "auth_oauth_identities"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    subject: Mapped[str] = mapped_column(String(255))
    """The provider's stable external ID for this identity (e.g. Google's `sub` claim)."""
    email: Mapped[str] = mapped_column(String(255))
    """Email at the time this identity was linked; not re-verified afterwards."""
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="identities")


class Token(Base):
    """An opaque, DB-backed session token. Only a hash of the token value is
    stored; the raw value is handed to the client once, at issuance, and is
    not recoverable from the database afterwards."""

    __tablename__ = "auth_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_type: Mapped[str] = mapped_column(String(20))
    """"cli" (VS Code) or "web" -- determines the expiry policy in provena.auth.tokens."""
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=_utcnow)
    last_used_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=_utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime())

    user: Mapped["User"] = relationship(back_populates="tokens")
