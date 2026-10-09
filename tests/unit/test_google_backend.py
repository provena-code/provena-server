"""T1: the Google backend's handling of Authlib's results
(provena.auth.backends.google), with Authlib's network calls stubbed out."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from provena.auth.backends.base import ExternalIdentity
from provena.auth.backends.google import GoogleOAuthBackend

FAKE_REQUEST = SimpleNamespace(query_params={"code": "c", "state": "s"}, session={})


@pytest.fixture
def backend():
    return GoogleOAuthBackend(client_id="id", client_secret="secret", hd="school.test")


def stub_token(monkeypatch, backend, result):
    async def authorize_access_token(request):
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(backend._oauth.google, "authorize_access_token", authorize_access_token)


def callback(backend):
    return asyncio.run(backend.callback(FAKE_REQUEST))


def test_verified_profile_becomes_an_identity(monkeypatch, backend):
    stub_token(monkeypatch, backend, {"userinfo": {
        "sub": "123", "email": "a@school.test", "email_verified": True, "name": "A",
    }})
    assert callback(backend) == ExternalIdentity(provider="google", subject="123", email="a@school.test", name="A")


def test_missing_email_verified_is_accepted(monkeypatch, backend):
    # [inferred] Only an explicit False is rejected.
    stub_token(monkeypatch, backend, {"userinfo": {"sub": "123", "email": "a@school.test"}})
    assert callback(backend).email == "a@school.test"


def test_unverified_email_is_rejected(monkeypatch, backend):
    stub_token(monkeypatch, backend, {"userinfo": {"sub": "123", "email": "a@school.test", "email_verified": False}})
    with pytest.raises(HTTPException) as error:
        callback(backend)
    assert error.value.status_code == 400
    assert "not verified" in error.value.detail


@pytest.mark.parametrize("token", [
    {},
    {"userinfo": None},
    {"userinfo": {"email": "a@school.test"}},
    {"userinfo": {"sub": "123"}},
    {"userinfo": {"sub": "", "email": "a@school.test"}},
])
def test_incomplete_profile_is_rejected(monkeypatch, backend, token):
    stub_token(monkeypatch, backend, token)
    with pytest.raises(HTTPException) as error:
        callback(backend)
    assert error.value.status_code == 400


def test_authlib_failure_becomes_400(monkeypatch, backend):
    stub_token(monkeypatch, backend, RuntimeError("mismatching_state"))
    with pytest.raises(HTTPException) as error:
        callback(backend)
    assert error.value.status_code == 400
    assert "mismatching_state" in error.value.detail


def test_login_passes_the_hosted_domain_hint(monkeypatch, backend):
    calls = []

    async def authorize_redirect(request, redirect_uri, **kwargs):
        calls.append((redirect_uri, kwargs))
        return "redirect"

    monkeypatch.setattr(backend._oauth.google, "authorize_redirect", authorize_redirect)
    assert asyncio.run(backend.login(FAKE_REQUEST, "https://server.test/cb")) == "redirect"
    assert calls == [("https://server.test/cb", {"hd": "school.test"})]
