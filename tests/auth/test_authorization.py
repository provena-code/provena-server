"""
T2: who can call what. Every API route is either in ROUTES below (with the
policy that guards it) or explicitly public; test_every_route_is_covered
fails when a new route shows up in neither.

Expected outcomes per (policy, credential) are spelled out in EXPECTED
rather than computed, so this table is the thing to review.
"""

import datetime as dt

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import update

from provena.auth.models import Token
from provena.config.configs import auth_config
from provena.db.base import SessionLocal

from tests.support.app_config import INSTRUCTOR_EMAIL, STUDENT_EMAIL
from tests.support.provena_helpers import instructor_key_headers, login_headers, submit_key_headers

SUBMIT_BODY = {
    "SubjectIDs": ["s1"], "AssignmentID": "A1", "ToolInstances": "autograder",
    "Score": 1.0, "ScoreDetails": None, "TermID": None, "CourseID": None,
    "CodeState": [{"CodeStateSection": "main.py", "Code": "x = 1"}],
}

# (method, path, request kwargs, policy)
ROUTES = [
    ("POST", "/events", {"json": []}, "student"),
    ("POST", "/get_event_count", {"json": {"SubjectIDs": ["s1"], "CodeState": []}}, "student"),
    ("POST", "/submit", {"json": SUBMIT_BODY}, "submit"),
    ("GET", "/read/assignments", {}, "instructor"),
    ("GET", "/read/assignments/{assignment_id}/subjects", {}, "instructor"),
    ("GET", "/read/assignments/{assignment_id}/{subject_id}/code_state_sections", {}, "instructor"),
    ("POST", "/read/update_mapping_table", {}, "instructor"),
    ("GET", "/read/subjects", {}, "instructor"),
    ("GET", "/read/subjects/{subject_id}/time_range", {}, "instructor"),
    ("GET", "/read/subjects/{subject_id}/codestate_sections", {}, "instructor"),
    ("GET", "/read/edits", {"params": {"subject_id": "s1", "codestate_section": "a.py"}}, "instructor"),
    ("GET", "/read/edits_in_range", {"params": {"subject_id": "s1", "start_client_timestamp": "0", "end_client_timestamp": "9"}}, "instructor"),
    ("GET", "/read/sessions/{session_id}/last_synced_order", {}, "public"),
]

# Routes with their own dedicated tests (tests/auth/test_login_flow.py).
AUTH_FLOW_ROUTES = {("GET", "/auth/login"), ("GET", "/auth/google/callback"), ("POST", "/auth/logout")}

OK, REAUTH, FORBIDDEN = 200, (401, "reauth_required"), (403, "insufficient_role")

#                         student     submit      instructor
EXPECTED = {
    "none":               (REAUTH,    REAUTH,     REAUTH),
    "garbage_bearer":     (REAUTH,    REAUTH,     REAUTH),
    "wrong_scheme":       (REAUTH,    REAUTH,     REAUTH),
    "expired_token":      (REAUTH,    REAUTH,     REAUTH),
    "wrong_key":          (REAUTH,    REAUTH,     REAUTH),
    "student_login":      (OK,        FORBIDDEN,  FORBIDDEN),
    "outsider_login":     (FORBIDDEN, FORBIDDEN,  FORBIDDEN),
    "instructor_login":   (OK,        OK,         OK),
    "instructor_key":     (OK,        OK,         OK),
    # D11: a valid credential without the role is a 403.
    "submit_key":         (FORBIDDEN, OK,         FORBIDDEN),
    "student_login+wrong_key":    (OK,  FORBIDDEN, FORBIDDEN),
    "student_login+submit_key":   (OK,  OK,        FORBIDDEN),
    "outsider_login+instructor_key": (OK, OK,      OK),
}
POLICY_COLUMN = {"student": 0, "submit": 1, "instructor": 2}

# Cells that don't match EXPECTED yet, by (credential, policy), with the bug.
KNOWN_BUGS: dict[tuple[str, str], str] = {}


def _expired_login() -> dict[str, str]:
    headers = login_headers(STUDENT_EMAIL)
    with SessionLocal() as db:
        db.execute(update(Token).values(expires_at=dt.datetime(2000, 1, 1)))
        db.commit()
    return headers


CREDENTIALS = {
    "none": lambda: {},
    "garbage_bearer": lambda: {"Authorization": "Bearer not-a-real-token"},
    "wrong_scheme": lambda: {"Authorization": "Token " + login_headers(INSTRUCTOR_EMAIL)["Authorization"].split()[1]},
    "expired_token": _expired_login,
    "wrong_key": lambda: {"X-API-Key": "wrong-key"},
    "student_login": lambda: login_headers(STUDENT_EMAIL),
    "outsider_login": lambda: login_headers("someone@elsewhere.test"),
    "instructor_login": lambda: login_headers(INSTRUCTOR_EMAIL),
    "instructor_key": instructor_key_headers,
    "submit_key": submit_key_headers,
    "student_login+wrong_key": lambda: {**login_headers(STUDENT_EMAIL), "X-API-Key": "wrong-key"},
    "student_login+submit_key": lambda: {**login_headers(STUDENT_EMAIL), **submit_key_headers()},
    "outsider_login+instructor_key": lambda: {**login_headers("someone@elsewhere.test"), **instructor_key_headers()},
}


def _url(path: str) -> str:
    return path.format(assignment_id="A1", subject_id="s1", session_id="sess1")


def _call(client, method, path, kwargs, headers):
    return client.request(method, _url(path), headers=headers, **kwargs)


def _assert_outcome(response, expected):
    if expected == OK:
        assert response.status_code == 200, response.text
    else:
        status, detail = expected
        assert (response.status_code, response.json().get("detail")) == (status, detail), response.text


@pytest.mark.parametrize("credential", list(EXPECTED))
@pytest.mark.parametrize("method, path, kwargs, policy", [r for r in ROUTES if r[3] != "public"],
                         ids=[f"{r[0]} {r[1]}" for r in ROUTES if r[3] != "public"])
def test_matrix(request, client, method, path, kwargs, policy, credential):
    if (credential, policy) in KNOWN_BUGS:
        request.applymarker(pytest.mark.xfail(reason=KNOWN_BUGS[(credential, policy)]))
    headers = CREDENTIALS[credential]()
    response = _call(client, method, path, kwargs, headers)
    _assert_outcome(response, EXPECTED[credential][POLICY_COLUMN[policy]])


@pytest.mark.parametrize("credential", ["none", "garbage_bearer", "outsider_login"])
def test_public_routes_need_no_credential(client, credential):
    for method, path, kwargs, policy in ROUTES:
        if policy == "public":
            assert _call(client, method, path, kwargs, CREDENTIALS[credential]()).status_code == 200


def test_every_route_is_covered(app):
    known = {(method, path) for method, path, _, _ in ROUTES} | AUTH_FLOW_ROUTES
    actual = {
        (method, route.path)
        for route in app.routes if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert actual - known == set(), "new routes need an entry in ROUTES (or AUTH_FLOW_ROUTES)"
    assert known - actual == set(), "ROUTES lists routes that no longer exist"


# --- Role configurations ------------------------------------------------------

def _set_role_type(monkeypatch, role: str, type_: str):
    monkeypatch.setattr(getattr(auth_config.roles, role), "type", type_)


@pytest.mark.parametrize("method, path, kwargs, policy", ROUTES, ids=[f"{r[0]} {r[1]}" for r in ROUTES])
def test_open_student_role(client, monkeypatch, method, path, kwargs, policy):
    """roles.student: open opens the student routes and /submit, but not
    the instructor ones."""
    _set_role_type(monkeypatch, "student", "open")
    response = _call(client, method, path, kwargs, {})
    expected = REAUTH if policy == "instructor" else OK
    _assert_outcome(response, expected)


@pytest.mark.parametrize("method, path, kwargs, policy", ROUTES, ids=[f"{r[0]} {r[1]}" for r in ROUTES])
def test_open_instructor_role(client, monkeypatch, method, path, kwargs, policy):
    _set_role_type(monkeypatch, "instructor", "open")
    _assert_outcome(_call(client, method, path, kwargs, {}), OK)


def test_open_student_role_lets_role_less_logins_through(client, monkeypatch):
    # With student open, even a login with no role passes the student gate:
    # the open check comes before the credential is looked at.
    _set_role_type(monkeypatch, "student", "open")
    response = client.post("/events", json=[], headers=login_headers("someone@elsewhere.test"))
    assert response.status_code == 200
