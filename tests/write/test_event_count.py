"""
T3: POST /get_event_count, which counts a subject's logged events for the
files being submitted: found by code hash, falling back to the file name,
and following renames back to earlier names.

Paths are unique per test (see `paths`). That was needed while B1 made the
rename search's "already checked" set shared by every call in the process;
it's kept as cheap insurance against order-dependent results.
"""

import uuid

import pytest

from tests.support.provena_helpers import event as any_event, seed_events, student_headers

CODE = "def solve():\n    return 42\n"


@pytest.fixture
def paths():
    """Unique directory per test; returns a function building paths in it."""
    root = f"proj-{uuid.uuid4().hex[:8]}"
    return lambda name: f"{root}/{name}"


@pytest.fixture
def count(client):
    headers = student_headers()

    def count(code_state, subjects=("s1",)):
        body = {"SubjectIDs": list(subjects), "CodeState": [
            {"CodeStateSection": section, "Code": code} for section, code in code_state
        ]}
        response = client.post("/get_event_count", json=body, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()
    return count


def event(event_type, **overrides):
    return any_event(event_type, **{"SubjectID": "s1", **overrides})


def edits(path, n, subject="s1", code=None):
    return [event("File.Edit", SubjectID=subject, CodeStateSection=path, EditType="Insert", Code=code) for _ in range(n)]


def test_counts_events_of_the_file_with_matching_code(count, paths):
    path = paths("main.py")
    seed_events(edits(path, 3) + [event("File.Save", CodeStateSection=path, Code=CODE)])
    # The submitted name is just the basename; the hash finds the real path.
    assert count([("main.py", CODE)]) == 4


def test_matching_code_ignores_line_endings(count, paths):
    path = paths("main.py")
    seed_events([event("File.Save", CodeStateSection=path, Code=CODE)])
    assert count([("whatever.py", CODE.replace("\n", "\r\n"))]) == 1


def test_only_the_given_subjects_events_count(count, paths):
    path = paths("main.py")
    seed_events(edits(path, 2) + edits(path, 5, subject="s2") + [event("File.Save", CodeStateSection=path, Code=CODE)])
    assert count([("main.py", CODE)]) == 3
    assert count([("main.py", CODE)], subjects=["s1", "s2"]) == 3 + 5


def test_submit_events_are_not_counted(count, paths):
    path = paths("main.py")
    seed_events([event("File.Save", CodeStateSection=path, Code=CODE),
                 event("Submit", CodeStateSection=path, Code=CODE)])
    assert count([("main.py", CODE)]) == 1


def test_no_events_counts_zero(count, paths):
    assert count([(paths("nothing.py"), CODE)]) == 0


def test_falls_back_to_the_path_suffix_when_no_code_matches(count, paths):
    path = paths("sub/main.py")
    seed_events(edits(path, 2) + [event("File.Save", CodeStateSection=path, Code="old code")])
    assert count([("main.py", "code that was never logged")]) == 3


def test_suffix_match_needs_a_whole_path_component(count, paths):
    seed_events(edits(paths("xmain.py"), 2))
    assert count([("main.py", "never logged")]) == 0


def test_suffix_match_also_takes_an_exact_path(count, paths):
    path = paths("main.py")
    seed_events(edits(path, 2))
    assert count([(path, "never logged")]) == 2


def test_suffix_match_is_case_insensitive(count, paths):
    # Q-b: comparisons follow MySQL's case-insensitive collation.
    seed_events(edits(paths("Main.py"), 2))
    assert count([("main.PY", "never logged")]) == 2


def test_code_match_suppresses_the_suffix_fallback(count, paths):
    # Once any file matches by hash, other same-named files aren't added.
    a, b = paths("a/main.py"), paths("b/main.py")
    seed_events([event("File.Save", CodeStateSection=a, Code=CODE)] + edits(b, 5))
    assert count([("main.py", CODE)]) == 1


def test_follows_renames_to_earlier_names(count, paths):
    old, new = paths("old.py"), paths("new.py")
    seed_events(
        edits(old, 2)
        + [event("File.Rename", CodeStateSection=old, DestinationCodeStateSection=new)]
        + [event("File.Save", CodeStateSection=new, Code=CODE)]
    )
    # 2 edits + the rename (logged under the old name) + the save.
    assert count([("new.py", CODE)]) == 4


def test_follows_a_chain_of_renames(count, paths):
    a, b, c = paths("a.py"), paths("b.py"), paths("c.py")
    seed_events(
        edits(a, 1)
        + [event("File.Rename", CodeStateSection=a, DestinationCodeStateSection=b)]
        + edits(b, 1)
        + [event("File.Rename", CodeStateSection=b, DestinationCodeStateSection=c)]
        + [event("File.Save", CodeStateSection=c, Code=CODE)]
    )
    assert count([("c.py", CODE)]) == 5


def test_rename_cycle_terminates(count, paths):
    a, b = paths("a.py"), paths("b.py")
    seed_events(
        [event("File.Rename", CodeStateSection=a, DestinationCodeStateSection=b),
         event("File.Rename", CodeStateSection=b, DestinationCodeStateSection=a),
         event("File.Save", CodeStateSection=a, Code=CODE)]
    )
    assert count([("a.py", CODE)]) == 3


def test_renames_by_other_subjects_are_ignored(count, paths):
    old, new = paths("old.py"), paths("new.py")
    seed_events(
        edits(old, 4, subject="s2")
        + [event("File.Rename", SubjectID="s2", CodeStateSection=old, DestinationCodeStateSection=new),
           event("File.Save", CodeStateSection=new, Code=CODE)]
    )
    assert count([("new.py", CODE)]) == 1


def test_repeat_calls_give_the_same_count(count, paths):
    old, new = paths("old.py"), paths("new.py")
    seed_events(
        edits(old, 2)
        + [event("File.Rename", CodeStateSection=old, DestinationCodeStateSection=new),
           event("File.Save", CodeStateSection=new, Code=CODE)]
    )
    first = count([("new.py", CODE)])
    assert count([("new.py", CODE)]) == first
