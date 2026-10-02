from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from provena.auth.db import SessionLocal
from provena.auth.models import Token, User
from provena.auth.tokens import resolve_token


def get_auth_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _reauth_required() -> HTTPException:
    # A distinct, machine-readable signal so clients (VS Code extension, web
    # app) can tell "please log in again" apart from other failures and kick
    # off /auth/login automatically instead of surfacing a generic error.
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail="reauth_required")


def get_current_token(authorization: Optional[str] = Header(default=None), db: Session = Depends(get_auth_db)) -> Token:
    scheme, _, raw_token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        raise _reauth_required()

    token = resolve_token(db, raw_token)
    if token is None:
        raise _reauth_required()
    return token


def get_current_user(token: Token = Depends(get_current_token)) -> User:
    return token.user
