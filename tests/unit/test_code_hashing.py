"""T1: code canonicalization and CodeStateID hashing (provena.api.logging.logging)."""

import hashlib

import pytest

from provena.api.logging.logging import add_codestate_ids, generate_code_hash, get_canonical_string


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# --- get_canonical_string -----------------------------------------------------

def test_strips_leading_bom():
    assert get_canonical_string("﻿x = 1") == "x = 1"


def test_keeps_bom_that_is_not_leading():
    assert get_canonical_string("x﻿") == "x﻿"


def test_converts_crlf_to_lf():
    assert get_canonical_string("a\r\nb\r\n") == "a\nb\n"


def test_leaves_lone_cr_alone():
    # [inferred] Only Windows line endings are normalized; a bare CR (classic
    # Mac) is left as-is.
    assert get_canonical_string("a\rb") == "a\rb"


def test_normalizes_to_nfc():
    decomposed = "café"  # e + combining acute accent
    assert get_canonical_string(decomposed) == "café"


# --- generate_code_hash -------------------------------------------------------

def test_hash_is_md5_of_stripped_code():
    assert generate_code_hash("  x = 1\n\n") == md5("x = 1")


def test_hash_keeps_internal_whitespace():
    assert generate_code_hash("x = 1") != generate_code_hash("x  = 1")


@pytest.mark.parametrize("variant", [
    "def f():\r\n    return 1\r\n",   # Windows line endings
    "﻿def f():\n    return 1\n",  # BOM
    "def f():\n    return 1",          # no trailing newline
    "\n\ndef f():\n    return 1\n  ",  # extra surrounding whitespace
])
def test_equivalent_code_from_different_clients_hashes_the_same(variant):
    assert generate_code_hash(variant) == generate_code_hash("def f():\n    return 1\n")


def test_canonicalize_false_hashes_raw_line_endings():
    assert generate_code_hash("a\r\nb", canonicalize=False) != generate_code_hash("a\nb", canonicalize=False)


# --- add_codestate_ids --------------------------------------------------------

def test_add_codestate_ids_only_sets_ids_on_events_with_code():
    with_code = {"Code": "x = 1\r\n"}
    without_code = {"EventType": "Session.Start"}

    add_codestate_ids([with_code, without_code])

    assert with_code["CodeStateID"] == generate_code_hash("x = 1\n")
    assert "CodeStateID" not in without_code


def test_add_codestate_ids_overwrites_a_client_supplied_id():
    # [inferred] The server is the authority on CodeStateIDs when Code is sent.
    event = {"Code": "x = 1", "CodeStateID": "client-id"}
    add_codestate_ids([event])
    assert event["CodeStateID"] == generate_code_hash("x = 1")


def test_add_codestate_ids_matches_the_hash_get_event_count_uses():
    # /get_event_count looks events up by generate_code_hash(Code), so the
    # stored ID must come out the same for the same code.
    code = "﻿print('hi')\r\n"
    event = {"Code": code}
    add_codestate_ids([event])
    assert event["CodeStateID"] == generate_code_hash(code)
