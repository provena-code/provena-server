"""T1: role matching, API key comparison (provena.auth.roles), and auth
config validation (provena.auth.config)."""

import logging

import pytest
from pydantic import ValidationError

from provena.auth.config import RoleConfig, RolesConfig
from provena.auth.roles import _matches_any_key, matches_role


def whitelist(*emails):
    return RoleConfig(type="whitelist", emails=list(emails))


def pattern(glob):
    return RoleConfig(type="pattern", pattern=glob)


# --- matches_role -------------------------------------------------------------

@pytest.mark.parametrize("email, expected", [
    ("prof@ncsu.edu", True),
    ("PROF@NCSU.EDU", True),       # case-insensitive both ways
    ("other@ncsu.edu", False),
    ("prof@ncsu.edu.evil.test", False),
    ("", False),
])
def test_whitelist(email, expected):
    assert matches_role(email, whitelist("Prof@NCSU.edu")) is expected


@pytest.mark.parametrize("email, expected", [
    ("student@ncsu.edu", True),
    ("Student@NCSU.EDU", True),
    ("student@sub.ncsu.edu", False),    # subdomains don't match "*@ncsu.edu"
    ("student@ncsu.edu.evil.test", False),
    ("student@evilncsu.edu", False),
    ("@ncsu.edu", True),                # "*" matches the empty string
])
def test_pattern(email, expected):
    assert matches_role(email, pattern("*@ncsu.edu")) is expected


def test_pattern_with_two_at_signs():
    # Pinned, harmless in practice: Google only returns real addresses.
    assert matches_role("evil@x@ncsu.edu", pattern("*@ncsu.edu"))


def test_open_matches_anyone():
    assert matches_role("anyone@anywhere.test", RoleConfig(type="open"))


def test_unknown_role_type_raises():
    role = RoleConfig.model_construct(type="blacklist")
    with pytest.raises(ValueError):
        matches_role("x@y.test", role)


# --- _matches_any_key ---------------------------------------------------------

@pytest.mark.parametrize("candidate, keys, expected", [
    ("k1", ["k1", "k2"], True),
    ("k2", ["k1", "k2"], True),
    ("k3", ["k1", "k2"], False),
    ("K1", ["k1"], False),          # keys are case-sensitive
    ("k1 ", ["k1"], False),
    (None, ["k1"], False),
    ("", ["k1"], False),
    ("", [""], False),              # an empty configured key never matches
    ("k1", [], False),
])
def test_matches_any_key(candidate, keys, expected):
    assert _matches_any_key(candidate, keys) is expected


@pytest.mark.xfail(raises=TypeError, reason="B13: secrets.compare_digest raises on non-ASCII str, so such an X-API-Key header gives a 500")
def test_non_ascii_key_is_rejected_not_raised():
    assert _matches_any_key("clé", ["k1"]) is False


# --- config validation --------------------------------------------------------

def test_whitelist_requires_emails():
    with pytest.raises(ValidationError, match="whitelist"):
        RoleConfig(type="whitelist")


def test_pattern_requires_pattern():
    with pytest.raises(ValidationError, match="pattern"):
        RoleConfig(type="pattern")


def test_unknown_type_is_rejected():
    with pytest.raises(ValidationError):
        RoleConfig(type="blacklist")


def _roles(instructor: dict, student: dict) -> RolesConfig:
    return RolesConfig(instructor=instructor, student=student)


def test_open_instructor_logs_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="provena.auth.config"):
        _roles({"type": "open"}, {"type": "open"})
    assert any("roles.instructor.type is 'open'" in r.message for r in caplog.records)


def test_missing_submit_keys_logs_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="provena.auth.config"):
        _roles({"type": "whitelist", "emails": ["p@x.test"]}, {"type": "pattern", "pattern": "*@x.test"})
    assert any("submit_api_keys is empty" in r.message for r in caplog.records)


def test_typical_config_logs_no_warnings(caplog):
    with caplog.at_level(logging.WARNING, logger="provena.auth.config"):
        _roles(
            {"type": "whitelist", "emails": ["p@x.test"], "api_keys": ["k"]},
            {"type": "pattern", "pattern": "*@x.test", "submit_api_keys": ["s"]},
        )
    assert not caplog.records
