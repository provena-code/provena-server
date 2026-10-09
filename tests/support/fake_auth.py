"""
A stand-in OAuth backend that drives the real /auth/login and
/auth/{backend}/callback endpoints without any network calls.
"""

from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

from fastapi import Request
from starlette.responses import RedirectResponse, Response

from provena.auth.backends.base import AuthBackend, ExternalIdentity


class FakeBackend(AuthBackend):
    """
    Registered under the name "google", since the callback route is
    per-backend. Set `identity` to choose who the next callback logs in.
    `before_callback`, if set, runs at the start of each callback (e.g. to
    hold threads at a barrier).
    """

    name = "google"

    def __init__(self):
        self.identity: Optional[ExternalIdentity] = None
        self.before_callback: Optional[Callable[[], None]] = None
        self.login_calls: list[str] = []

    async def login(self, request: Request, callback_url: str) -> Response:
        self.login_calls.append(callback_url)
        return RedirectResponse("https://accounts.fake.test/authorize")

    async def callback(self, request: Request) -> ExternalIdentity:
        if self.before_callback:
            self.before_callback()
        assert self.identity is not None, "set FakeBackend.identity before the callback"
        return self.identity


def identity(email: str, subject: Optional[str] = None, name: Optional[str] = None) -> ExternalIdentity:
    return ExternalIdentity(provider="google", subject=subject or f"sub-{email}", email=email, name=name)


def redirect_params(location: str) -> dict[str, str]:
    """The params a login callback delivered to the client, from the query
    ("cli") or the fragment ("web")."""
    parts = urlsplit(location)
    raw = parts.fragment if parts.fragment else parts.query
    return {key: values[-1] for key, values in parse_qs(raw).items()}
