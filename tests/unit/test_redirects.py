"""T1: client_redirect_uri allowlisting and token-delivery URL building
(provena.auth.redirects)."""

from urllib.parse import parse_qs, urlsplit

import pytest

from provena.auth.redirects import append_fragment_params, append_query_params, is_allowed_redirect_uri

ALLOWLIST = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "https://webapp.test",
    "https://exact.test:8443",
]


@pytest.mark.parametrize("uri", [
    "http://127.0.0.1:54321/callback",
    "http://127.0.0.1/callback",               # ":*" also allows no port
    "http://localhost:3000/",
    "https://webapp.test/auth/callback?x=1",
    "https://WEBAPP.test/cb",                  # hostnames are case-insensitive
    "https://exact.test:8443/cb",
])
def test_allowed(uri):
    assert is_allowed_redirect_uri(uri, ALLOWLIST)


@pytest.mark.parametrize("uri", [
    "https://127.0.0.1:5000/cb",                # scheme mismatch
    "http://webapp.test/cb",                    # scheme mismatch
    "https://webapp.test:8443/cb",              # port mismatch
    "https://exact.test/cb",                    # port mismatch
    "https://exact.test:9999/cb",
    "https://evil.test/cb",
    "http://127.0.0.1.evil.test:80/",           # allowed host as a prefix
    "http://evil.test/?next=http://127.0.0.1",
    "http://127.0.0.1:80@evil.test/",           # userinfo trick: host is evil.test
    "http://evil.test#@127.0.0.1",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "//webapp.test/cb",                         # no scheme
    "http:///cb",                               # no host
    "",
    "http://[::1]:5000/cb",                     # IPv6 loopback isn't listed
])
def test_rejected(uri):
    assert not is_allowed_redirect_uri(uri, ALLOWLIST)


def test_empty_allowlist_rejects_everything():
    assert not is_allowed_redirect_uri("http://127.0.0.1:5000/", [])


@pytest.mark.parametrize("uri, entry", [
    ("https://webapp.test:443/cb", "https://webapp.test"),
    ("http://127.0.0.1:80/cb", "http://127.0.0.1"),
    ("https://webapp.test/cb", "https://webapp.test:443"),
])
def test_default_port_is_the_same_as_no_port(uri, entry):
    # D10
    assert is_allowed_redirect_uri(uri, [entry])


def test_default_port_of_the_other_scheme_still_mismatches():
    assert not is_allowed_redirect_uri("https://webapp.test:80/cb", ["https://webapp.test"])


@pytest.mark.parametrize("uri", [
    "https://webapp.test:abc/cb",    # B12: used to raise ValueError (a 500)
    "https://exact.test:99999/cb",   # out of range
    "http://127.0.0.1:abc/cb",       # even on a ":*" host
])
def test_malformed_port_is_rejected_not_raised(uri):
    assert not is_allowed_redirect_uri(uri, ALLOWLIST)


def test_malformed_allowlist_entry_matches_nothing():
    assert not is_allowed_redirect_uri("https://webapp.test/cb", ["https://webapp.test:abc"])


# --- URL building -------------------------------------------------------------

def test_append_query_params_keeps_existing_query_and_fragment():
    uri = append_query_params("http://127.0.0.1:5000/cb?existing=1&blank=#frag", {"token": "t", "email": "a@b.test"})
    parts = urlsplit(uri)
    assert parse_qs(parts.query, keep_blank_values=True) == {
        "existing": ["1"], "blank": [""], "token": ["t"], "email": ["a@b.test"],
    }
    assert parts.fragment == "frag"


def test_append_query_params_encodes_values():
    uri = append_query_params("http://127.0.0.1/cb", {"state": "a&b=c d"})
    assert parse_qs(urlsplit(uri).query) == {"state": ["a&b=c d"]}


def test_append_fragment_params_leaves_query_alone():
    uri = append_fragment_params("https://webapp.test/cb?keep=1", {"token": "t"})
    parts = urlsplit(uri)
    assert parts.query == "keep=1"
    assert parse_qs(parts.fragment) == {"token": ["t"]}


def test_append_fragment_params_appends_after_an_existing_fragment():
    uri = append_fragment_params("https://webapp.test/#/route", {"token": "t"})
    assert urlsplit(uri).fragment == "/route&token=t"
