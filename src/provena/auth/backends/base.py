from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from fastapi import Request
from starlette.responses import Response


@dataclass
class ExternalIdentity:
    provider: str
    subject: str
    """The provider's stable, unique identifier for this identity (e.g. Google's `sub` claim)."""
    email: str
    name: Optional[str] = None


class AuthBackend(ABC):
    """
    A pluggable login method. A deployment configures exactly one active
    backend (see auth_config.yaml's `active_backend`); each backend's only
    job is to end up with a verified ExternalIdentity, which the generic
    /auth router turns into a local User + issued token.
    """

    name: str

    @abstractmethod
    async def login(self, request: Request, callback_url: str) -> Response:
        """Starts a login, typically by redirecting the browser to an external provider."""

    @abstractmethod
    async def callback(self, request: Request) -> ExternalIdentity:
        """Completes a login previously started by `login`, returning the verified identity."""
