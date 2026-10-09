"""T1: choosing and caching the auth backend (provena.auth.backends.registry)."""

import pytest

from provena.auth.backends import registry
from provena.auth.backends.google import GoogleOAuthBackend
from provena.config.configs import auth_config


@pytest.fixture
def empty_cache(monkeypatch):
    monkeypatch.setattr(registry, "_backends", {})


def test_active_backend_is_built_from_config_and_cached(empty_cache):
    backend = registry.get_active_backend()
    assert isinstance(backend, GoogleOAuthBackend)
    assert registry.get_active_backend() is backend


def test_unknown_backend_name_is_an_error(empty_cache):
    with pytest.raises(ValueError, match="Unknown auth backend"):
        registry.get_backend("github")


def test_google_without_its_config_section_is_an_error(empty_cache, monkeypatch):
    monkeypatch.setattr(auth_config.backends, "google", None)
    with pytest.raises(ValueError, match="no backends.google section"):
        registry.get_backend("google")
