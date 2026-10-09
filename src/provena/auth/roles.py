import fnmatch
import secrets
from typing import List, Optional

from fastapi import Depends, Header, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from provena.auth.config import RoleConfig
from provena.auth.dependencies import _reauth_required, get_auth_db
from provena.auth.models import User
from provena.auth.tokens import resolve_token
from provena.config.configs import auth_config

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def matches_role(email: str, role: RoleConfig) -> bool:
    if role.type == "open":
        return True
    email = email.lower()
    if role.type == "whitelist":
        return email in {e.lower() for e in role.emails}
    if role.type == "pattern":
        return fnmatch.fnmatchcase(email, role.pattern.lower())
    raise ValueError(f"Unknown role type: {role.type!r}")


def _matches_any_key(candidate: Optional[str], keys: List[str]) -> bool:
    # Constant-time per comparison (secrets.compare_digest), so a wrong guess
    # can't be timed against individual keys. Checking a candidate against
    # N keys in sequence still only takes O(N) comparisons either way --
    # that's fine here, since these lists are short and not secret in count.
    if not candidate:
        return False
    return any(secrets.compare_digest(candidate, key) for key in keys)


def _resolve_optional_user(authorization: Optional[str], db: Session) -> Optional[User]:
    scheme, _, raw_token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        return None
    token = resolve_token(db, raw_token)
    return token.user if token else None


def require_instructor_role(
    authorization: Optional[str] = Header(default=None),
    api_key: Optional[str] = Security(api_key_header),
    db: Session = Depends(get_auth_db),
) -> Optional[User]:
    """
    Instructor-only gate: an instructor-role API key, or a login matching the
    instructor role. Replaces the old placeholder `require_api_key`.
    """
    return _require_role(student_ok=False, authorization=authorization, api_key=api_key, db=db)


def require_student_role(
    authorization: Optional[str] = Header(default=None),
    api_key: Optional[str] = Security(api_key_header),
    db: Session = Depends(get_auth_db),
) -> Optional[User]:
    """
    Student-or-instructor gate, for the logging endpoints a student's own VS
    Code session calls directly. Instructors can do everything a student can,
    so an instructor credential satisfies this too. There is no
    general-purpose "student role" API key -- see require_submit_permission
    for the narrower, autograder-facing credential.
    """
    return _require_role(student_ok=True, authorization=authorization, api_key=api_key, db=db)


def _require_role(
    *,
    student_ok: bool,
    authorization: Optional[str],
    api_key: Optional[str],
    db: Session,
) -> Optional[User]:
    instructor = auth_config.roles.instructor
    student = auth_config.roles.student

    if instructor.type == "open":
        return None
    if student_ok and student.type == "open":
        return None
    if _matches_any_key(api_key, instructor.api_keys):
        return None

    user = _resolve_optional_user(authorization, db)
    if user is not None:
        if matches_role(user.email, instructor):
            return user
        if student_ok and matches_role(user.email, student):
            return user
        # A real, currently-valid identity -- it just doesn't have this role.
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="insufficient_role")

    raise _reauth_required()


def require_submit_permission(
    authorization: Optional[str] = Header(default=None),
    api_key: Optional[str] = Security(api_key_header),
    db: Session = Depends(get_auth_db),
) -> Optional[User]:
    """
    Gate for /submit specifically. Unlike require_student_role, a plain
    student OAuth login is NOT accepted here by design: /submit is expected
    to be called by the autograder (not the student's own VS Code session),
    and a student's login doesn't vouch for the autograder's legitimacy. The
    autograder is expected to present a roles.student.submit_api_keys key
    instead. Instructor credentials and the student role's "open" setting
    still work the same as everywhere else.
    """
    instructor = auth_config.roles.instructor
    student = auth_config.roles.student

    if student.type == "open":
        return None
    if instructor.type == "open":
        return None
    if _matches_any_key(api_key, instructor.api_keys) or _matches_any_key(api_key, student.submit_api_keys):
        return None

    user = _resolve_optional_user(authorization, db)
    if user is not None:
        if matches_role(user.email, instructor):
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="insufficient_role")

    raise _reauth_required()
