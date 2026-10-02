import logging
logger = logging.getLogger(__name__)

from typing import List, Literal, Optional

import yaml
from pydantic import BaseModel, model_validator


class GoogleBackendConfig(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: str
    """Must exactly match a redirect URI registered for this OAuth client in
    the Google Cloud Console credentials page."""

    hd: Optional[str] = None
    """Optional Google Workspace hosted-domain hint (e.g. "ncsu.edu"), passed
    to Google's authorization request to restrict the account picker to that
    domain. This is a UX nicety only, NOT a security boundary -- it doesn't
    stop someone from requesting a login without it and nothing here
    verifies it against the returned token, so the roles.student/.instructor
    whitelist/pattern checks remain the actual enforcement. Set explicitly;
    not inferred from a role's pattern."""


class BackendsConfig(BaseModel):
    google: Optional[GoogleBackendConfig] = None


class TokenConfig(BaseModel):
    cli_ttl_days: int = 90
    """Sliding expiry for VS Code / CLI tokens: each authenticated request
    extends expires_at by this many days, so an actively-used token
    effectively never forces a re-login; only a long-idle one expires."""

    web_ttl_hours: int = 12
    """Fixed expiry for web app tokens."""


RoleType = Literal["whitelist", "pattern", "open"]


class RoleConfig(BaseModel):
    """
    Determines which logged-in users (by email) are granted a role.
    * "whitelist": only the exact emails in `emails`.
    * "pattern": any email matching the glob-style `pattern` (e.g.
      "*@ncsu.edu"), matched case-insensitively.
    * "open": any authenticated user qualifies -- and no credential (API key
      or login) is required at all. See provena.auth.roles for how this is
      enforced.
    There is no blacklist mechanism.
    """

    type: RoleType
    emails: List[str] = []
    pattern: Optional[str] = None

    @model_validator(mode="after")
    def _check_fields_for_type(self) -> "RoleConfig":
        if self.type == "whitelist" and not self.emails:
            raise ValueError('roles: type "whitelist" requires a non-empty "emails" list.')
        if self.type == "pattern" and not self.pattern:
            raise ValueError('roles: type "pattern" requires a "pattern" string.')
        return self


class InstructorRoleConfig(RoleConfig):
    api_keys: List[str] = []
    """Full instructor privilege (grants everything a student can do too).
    Meant to stay private -- instructor/dev use, testing, admin scripts."""


class StudentRoleConfig(RoleConfig):
    submit_api_keys: List[str] = []
    """Grants ONLY /submit permission, nothing else -- not general student
    access. Meant for the autograder, which is less trusted than an
    instructor's own machine; there is no general-purpose "student role" API
    key, since a plain student identity is always proven via OAuth login."""


class RolesConfig(BaseModel):
    instructor: InstructorRoleConfig
    student: StudentRoleConfig

    @model_validator(mode="after")
    def _warn_on_risky_config(self) -> "RolesConfig":
        if self.instructor.type == "open":
            logger.warning(
                "auth_config.yaml: roles.instructor.type is 'open' -- any authenticated "
                "user (or no credential at all) will be granted instructor access, "
                "including read access to all student data."
            )
        if self.student.type != "open" and not self.student.submit_api_keys:
            logger.warning(
                "auth_config.yaml: roles.student.submit_api_keys is empty and "
                "roles.student.type is not 'open' -- /submit will be uncallable by "
                "anything other than an instructor credential (e.g. the autograder "
                "won't be able to call it)."
            )
        return self


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
    roles: RolesConfig

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "AuthConfig":
        with open(yaml_path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
        return cls(**data)
