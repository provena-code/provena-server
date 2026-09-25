import logging
logger = logging.getLogger(__name__)

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from provena.auth.backends.registry import get_active_backend, get_backend
from provena.auth.db import Base, engine
from provena.auth.dependencies import get_auth_db, get_current_token
from provena.auth.models import OAuthIdentity, Token, User
from provena.auth.redirects import append_query_params, is_allowed_redirect_uri
from provena.auth.tokens import issue_token, revoke_token
from provena.configs import auth_config

# Create the auth tables if they don't already exist. These are hand-written
# SQLAlchemy models (see provena.auth.models), not generated from the
# ProgSnap2 spec, though they live in the same database as the logging data.
try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    logger.error(f"Error initializing auth database: {e}")

router = APIRouter(prefix="/auth", tags=["auth"])

# Callback URLs are per-backend and fixed (they must exactly match what's
# registered with the provider), unlike /auth/login, which is generic.
_CALLBACK_URLS = {
    "google": lambda: auth_config.backends.google.redirect_uri,
}


def _require_allowed_redirect_uri(uri: str) -> None:
    if not is_allowed_redirect_uri(uri, auth_config.redirect_allowlist):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "client_redirect_uri is not in the allowed list.")


@router.get("/login", operation_id="authLogin")
async def login(
    request: Request,
    client_redirect_uri: str,
    client_type: Literal["cli", "web"] = "web",
    state: Optional[str] = None,
):
    """
    Starts a login using whichever backend is configured as active for this
    server. Neither client needs to know which backend that is.

    `client_redirect_uri` is where the browser is sent (with a token
    attached) once login completes -- the VS Code extension's local loopback
    server, or the web app's own callback route. It must match an entry in
    auth_config.yaml's redirect_allowlist.

    `state`, if given, is treated as an opaque value and returned unchanged as
    a `state` query param on that same final redirect. This is the client's
    own CSRF defense, not the server's: a client should generate a random
    value here, remember it (e.g. in memory before opening the browser), and
    verify the returned `state` matches before trusting the returned token --
    otherwise a stray or spoofed request landing on the client's callback
    (loopback server / callback route) could be accepted as a real login.
    This is separate from, and in addition to, the OAuth CSRF state Authlib
    already manages between this server and Google.
    """
    _require_allowed_redirect_uri(client_redirect_uri)
    request.session["client_redirect_uri"] = client_redirect_uri
    request.session["client_type"] = client_type
    if state is not None:
        request.session["client_state"] = state

    backend = get_active_backend()
    callback_url = _CALLBACK_URLS[backend.name]()
    return await backend.login(request, callback_url)


@router.get("/google/callback", operation_id="authGoogleCallback", include_in_schema=False)
async def google_callback(request: Request, db: Session = Depends(get_auth_db)):
    identity = await get_backend("google").callback(request)

    client_redirect_uri = request.session.pop("client_redirect_uri", None)
    client_type = request.session.pop("client_type", "web")
    client_state = request.session.pop("client_state", None)
    if not client_redirect_uri:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Login session expired or was not started via /auth/login.")

    identity_row = db.execute(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == identity.provider,
            OAuthIdentity.subject == identity.subject,
        )
    ).scalar_one_or_none()

    if identity_row is not None:
        user = identity_row.user
    else:
        user = db.execute(select(User).where(User.email == identity.email)).scalar_one_or_none()
        if user is None:
            user = User(email=identity.email, display_name=identity.name)
            db.add(user)
            db.flush()
        db.add(OAuthIdentity(
            user_id=user.id,
            provider=identity.provider,
            subject=identity.subject,
            email=identity.email,
        ))
    db.commit()

    raw_token, token = issue_token(db, user, client_type)
    params = {
        "token": raw_token,
        "email": user.email,
        # Naive UTC (see provena.auth.tokens), so make that explicit for clients.
        "expires_at": token.expires_at.isoformat() + "Z",
    }
    if user.display_name:
        params["name"] = user.display_name
    if client_state is not None:
        params["state"] = client_state
    return RedirectResponse(append_query_params(client_redirect_uri, params))


@router.post("/logout", operation_id="authLogout")
def logout(token: Token = Depends(get_current_token), db: Session = Depends(get_auth_db)):
    """
    Revokes the caller's own token. Logging out elsewhere (e.g. other devices)
    is unaffected.
    """
    revoke_token(db, token)
    return {"success": True}
