"""T2: the login flow end to end (/auth/login -> /auth/google/callback ->
token), with FakeBackend standing in for Google, plus /auth/logout."""

import pytest
from sqlalchemy import func, select

from provena.auth.models import OAuthIdentity, Token, User
from provena.db.base import SessionLocal

from tests.support.app_config import INSTRUCTOR_EMAIL, STUDENT_EMAIL
from tests.support.fake_auth import identity, redirect_params

CLI_REDIRECT = "http://127.0.0.1:5123/callback"
WEB_REDIRECT = "https://webapp.test/auth/done"


def log_in(client, backend, who, client_type="cli", redirect=CLI_REDIRECT, state=None):
    """Runs the whole flow; returns the callback response."""
    params = {"client_redirect_uri": redirect, "client_type": client_type}
    if state is not None:
        params["state"] = state
    response = client.get("/auth/login", params=params, follow_redirects=False)
    assert response.status_code in (302, 307), response.text
    backend.identity = who
    return client.get("/auth/google/callback", follow_redirects=False)


def counts():
    with SessionLocal() as db:
        return {model.__tablename__: db.execute(select(func.count()).select_from(model)).scalar_one()
                for model in (User, OAuthIdentity, Token)}


# --- /auth/login --------------------------------------------------------------

def test_login_hands_off_to_the_backend_with_its_callback_url(https_client, fake_backend):
    response = https_client.get("/auth/login", params={"client_redirect_uri": CLI_REDIRECT}, follow_redirects=False)
    assert response.headers["location"] == "https://accounts.fake.test/authorize"
    assert fake_backend.login_calls == ["http://testserver/auth/google/callback"]


@pytest.mark.parametrize("redirect", ["https://evil.test/cb", "http://127.0.0.1:80@evil.test/", "javascript:alert(1)"])
def test_login_rejects_redirects_outside_the_allowlist(https_client, fake_backend, redirect):
    response = https_client.get("/auth/login", params={"client_redirect_uri": redirect}, follow_redirects=False)
    assert response.status_code == 400
    assert fake_backend.login_calls == []


# --- /auth/google/callback ----------------------------------------------------

def test_first_login_creates_user_identity_and_token(https_client, fake_backend):
    response = log_in(https_client, fake_backend, identity(STUDENT_EMAIL, "sub-1", name="Stu"))

    assert response.status_code == 307
    assert response.headers["location"].startswith(CLI_REDIRECT + "?")
    params = redirect_params(response.headers["location"])
    assert params["email"] == STUDENT_EMAIL
    assert params["name"] == "Stu"
    assert params["expires_at"].endswith("Z")
    assert counts() == {"auth_users": 1, "auth_oauth_identities": 1, "auth_tokens": 1}


def test_issued_token_works_on_the_api(https_client, fake_backend):
    token = redirect_params(log_in(https_client, fake_backend, identity(STUDENT_EMAIL)).headers["location"])["token"]
    response = https_client.post("/events", json=[], headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_repeat_login_reuses_the_user(https_client, fake_backend):
    log_in(https_client, fake_backend, identity(STUDENT_EMAIL, "sub-1"))
    log_in(https_client, fake_backend, identity(STUDENT_EMAIL, "sub-1"))
    assert counts() == {"auth_users": 1, "auth_oauth_identities": 1, "auth_tokens": 2}


def test_same_email_new_subject_links_to_the_existing_user(https_client, fake_backend):
    log_in(https_client, fake_backend, identity(STUDENT_EMAIL, "sub-1"))
    log_in(https_client, fake_backend, identity(STUDENT_EMAIL, "sub-2"))
    assert counts() == {"auth_users": 1, "auth_oauth_identities": 2, "auth_tokens": 2}


def test_email_differing_only_in_case_is_the_same_user(https_client, fake_backend):
    # [inferred] The lookup compares under MySQL's case-insensitive
    # collation, so these are one user (keeping the first-seen spelling).
    log_in(https_client, fake_backend, identity("Prof@Instructor.test", "sub-1"))
    log_in(https_client, fake_backend, identity("prof@instructor.test", "sub-2"))
    assert counts()["auth_users"] == 1
    with SessionLocal() as db:
        assert db.execute(select(User.email)).scalar_one() == "Prof@Instructor.test"


def test_existing_identity_keeps_its_user_even_if_the_email_changed(https_client, fake_backend):
    # Pinned: the user is found by (provider, subject); the stored email
    # isn't updated, so role checks keep using the old address.
    log_in(https_client, fake_backend, identity(STUDENT_EMAIL, "sub-1"))
    response = log_in(https_client, fake_backend, identity("renamed@student.test", "sub-1"))
    assert redirect_params(response.headers["location"])["email"] == STUDENT_EMAIL


def test_web_login_delivers_the_token_in_the_fragment(https_client, fake_backend):
    response = log_in(https_client, fake_backend, identity(INSTRUCTOR_EMAIL), client_type="web", redirect=WEB_REDIRECT)
    location = response.headers["location"]
    assert location.startswith(WEB_REDIRECT + "#")
    assert "token=" not in location.split("#")[0]
    assert "token" in redirect_params(location)


def test_client_type_defaults_to_web(https_client, fake_backend):
    https_client.get("/auth/login", params={"client_redirect_uri": WEB_REDIRECT}, follow_redirects=False)
    fake_backend.identity = identity(INSTRUCTOR_EMAIL)
    response = https_client.get("/auth/google/callback", follow_redirects=False)
    assert "#" in response.headers["location"]
    with SessionLocal() as db:
        assert db.execute(select(Token.client_type)).scalar_one() == "web"


def test_state_is_echoed_back(https_client, fake_backend):
    response = log_in(https_client, fake_backend, identity(STUDENT_EMAIL), state="xyz-123")
    assert redirect_params(response.headers["location"])["state"] == "xyz-123"


def test_no_state_param_when_none_was_given(https_client, fake_backend):
    response = log_in(https_client, fake_backend, identity(STUDENT_EMAIL))
    assert "state" not in redirect_params(response.headers["location"])


def test_callback_without_login_is_rejected(https_client, fake_backend):
    fake_backend.identity = identity(STUDENT_EMAIL)
    response = https_client.get("/auth/google/callback", follow_redirects=False)
    assert response.status_code == 400
    assert counts() == {"auth_users": 0, "auth_oauth_identities": 0, "auth_tokens": 0}


def test_callback_cannot_be_replayed(https_client, fake_backend):
    log_in(https_client, fake_backend, identity(STUDENT_EMAIL))
    response = https_client.get("/auth/google/callback", follow_redirects=False)
    assert response.status_code == 400


def test_session_cookie_is_secure(https_client, fake_backend):
    response = https_client.get("/auth/login", params={"client_redirect_uri": CLI_REDIRECT}, follow_redirects=False)
    assert "secure" in response.headers["set-cookie"].lower()


def test_login_users_without_a_role_still_get_a_token(https_client, fake_backend):
    # Pinned: login doesn't check roles; the token just won't pass any gate.
    response = log_in(https_client, fake_backend, identity("someone@elsewhere.test"))
    token = redirect_params(response.headers["location"])["token"]
    response = https_client.post("/events", json=[], headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


# --- /auth/logout -------------------------------------------------------------

def test_logout_revokes_only_the_callers_token(https_client, fake_backend):
    token_a = redirect_params(log_in(https_client, fake_backend, identity(STUDENT_EMAIL)).headers["location"])["token"]
    token_b = redirect_params(log_in(https_client, fake_backend, identity(STUDENT_EMAIL)).headers["location"])["token"]
    a, b = ({"Authorization": f"Bearer {t}"} for t in (token_a, token_b))

    assert https_client.post("/auth/logout", headers=a).json() == {"success": True}

    assert https_client.post("/events", json=[], headers=a).status_code == 401
    assert https_client.post("/events", json=[], headers=b).status_code == 200


def test_logout_without_a_token_is_401(https_client):
    response = https_client.post("/auth/logout")
    assert (response.status_code, response.json()["detail"]) == (401, "reauth_required")


def test_logout_with_an_api_key_is_401(https_client):
    response = https_client.post("/auth/logout", headers={"X-API-Key": "test-instructor-key"})
    assert response.status_code == 401
