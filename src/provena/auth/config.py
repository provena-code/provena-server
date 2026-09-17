from typing import Optional

import yaml
from pydantic import BaseModel


class GoogleBackendConfig(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: str
    """Must exactly match a redirect URI registered for this OAuth client in
    the Google Cloud Console credentials page."""


class BackendsConfig(BaseModel):
    google: Optional[GoogleBackendConfig] = None


class TokenConfig(BaseModel):
    cli_ttl_days: int = 90
    """Sliding expiry for VS Code / CLI tokens: each authenticated request
    extends expires_at by this many days, so an actively-used token
    effectively never forces a re-login; only a long-idle one expires."""

    web_ttl_hours: int = 12
    """Fixed expiry for web app tokens."""


class AuthConfig(BaseModel):
    active_backend: str
    """Name of the single auth backend this deployment uses (e.g. "google").
    Only one backend is active per deployment; different deployments of this
    server (e.g. for a different institution) may configure a different one."""

    session_secret_key: str
    """Secret key for signing the short-lived, server-side login session
    cookie (OAuth state, plus the client_redirect_uri/client_type passed to
    /auth/login). Not used for issued API tokens, which are opaque and
    DB-backed."""

    redirect_allowlist: list[str] = []
    """Origins a client is allowed to request as `client_redirect_uri` on
    /auth/login -- see provena.auth.redirects for the pattern syntax."""

    backends: BackendsConfig = BackendsConfig()
    token: TokenConfig = TokenConfig()

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "AuthConfig":
        with open(yaml_path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
        return cls(**data)
