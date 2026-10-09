"""T4: /read/edits (a file's history, following renames, optionally cut off
at a code state) and /read/edits_in_range."""

import pytest

from tests.support.progsnap2_events import timestamp
from tests.support.provena_helpers import event as any_event, instructor_key_headers, seed_events


def event(event_type, **overrides):
    return any_event(event_type, **{"SubjectID": "s1", **overrides})


def edit(path, order, **overrides):
    return event("File.Edit", CodeStateSection=path, EditType="Insert", Order=order, **overrides)


def rename(source, destination, order):
    return event("File.Rename", CodeStateSection=source, DestinationCodeStateSection=destination, Order=order)


@pytest.fixture
def get_edits(client):
    headers = instructor_key_headers()

    def get_edits(path, subject="s1", last_codestate_id=None):
        params = {"subject_id": subject, "codestate_section": path}
        if last_codestate_id:
            params["last_codestate_id"] = last_codestate_id
        response = client.get("/read/edits", params=params, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()
    return get_edits


def orders(rows):
    return [row["Order"] for row in rows]


def test_returns_the_files_events_in_time_order(get_edits):
    seed_events([edit("a.py", 3), edit("a.py", 1), edit("a.py", 2), edit("b.py", 4),
                 edit("a.py", 5, SubjectID="s2")])
    assert orders(get_edits("a.py")) == [1, 2, 3]


def test_ties_on_timestamp_are_broken_by_order(get_edits):
    same = timestamp(100)
    seed_events([edit("a.py", 2, ClientTimestamp=same), edit("a.py", 1, ClientTimestamp=same)])
    assert orders(get_edits("a.py")) == [1, 2]


def test_null_columns_are_left_out(get_edits):
    seed_events([edit("a.py", 1)])
    [row] = get_edits("a.py")
    assert all(value is not None for value in row.values())
    assert "Score" not in row


def test_events_without_a_client_timestamp_are_left_out(get_edits):
    # e.g. Submit events, which the server creates.
    seed_events([edit("a.py", 1), edit("a.py", 2, ClientTimestamp=None)])
    assert orders(get_edits("a.py")) == [1]


def test_unknown_file_has_no_edits(get_edits):
    assert get_edits("nothing.py") == []


def test_history_follows_a_rename(get_edits):
    seed_events([edit("old.py", 1), edit("old.py", 2), rename("old.py", "new.py", 3), edit("new.py", 4)])
    assert orders(get_edits("new.py")) == [1, 2, 3, 4]


def test_history_follows_a_chain_of_renames(get_edits):
    seed_events([edit("a.py", 1), rename("a.py", "b.py", 2), edit("b.py", 3),
                 rename("b.py", "c.py", 4), edit("c.py", 5)])
    assert orders(get_edits("c.py")) == [1, 2, 3, 4, 5]


def test_a_new_file_reusing_the_old_name_is_not_included(get_edits):
    seed_events([edit("old.py", 1), rename("old.py", "new.py", 2), edit("new.py", 3),
                 edit("old.py", 4)])  # a different file, created later under the old name
    assert orders(get_edits("new.py")) == [1, 2, 3]


def test_events_before_the_new_name_existed_are_not_included(get_edits):
    # A file that had the new name before the rename was a different file.
    seed_events([edit("new.py", 1), edit("old.py", 2), rename("old.py", "new.py", 3), edit("new.py", 4)])
    assert orders(get_edits("new.py")) == [2, 3, 4]


def test_renames_by_other_subjects_are_ignored(get_edits):
    seed_events([edit("old.py", 1, SubjectID="s2"), rename("old.py", "new.py", 2) | {"SubjectID": "s2"},
                 edit("new.py", 3)])
    assert orders(get_edits("new.py")) == [3]


# --- last_codestate_id cutoff ---------------------------------------------------

def test_cutoff_stops_at_the_first_event_with_that_code(get_edits):
    from provena.api.logging.logging import generate_code_hash
    seed_events([edit("a.py", 1), edit("a.py", 2, Code="v1"), edit("a.py", 3), edit("a.py", 4, Code="v1"), edit("a.py", 5)])
    assert orders(get_edits("a.py", last_codestate_id=generate_code_hash("v1"))) == [1, 2]


def test_cutoff_includes_events_at_the_same_timestamp(get_edits):
    from provena.api.logging.logging import generate_code_hash
    same = timestamp(2)
    seed_events([edit("a.py", 1), edit("a.py", 2, Code="v1", ClientTimestamp=same),
                 edit("a.py", 3, ClientTimestamp=same), edit("a.py", 4)])
    assert orders(get_edits("a.py", last_codestate_id=generate_code_hash("v1"))) == [1, 2, 3]


def test_cutoff_applies_across_a_rename(get_edits):
    from provena.api.logging.logging import generate_code_hash
    seed_events([edit("old.py", 1), rename("old.py", "new.py", 2), edit("new.py", 3, Code="v1"), edit("new.py", 4)])
    assert orders(get_edits("new.py", last_codestate_id=generate_code_hash("v1"))) == [1, 2, 3]


def test_cutoff_before_a_rename_ignores_the_rename(get_edits):
    # The code was reached before the file was renamed, so only the old
    # name's history up to that point counts... but the query asks about
    # the *new* name, whose range then starts after the cutoff.
    from provena.api.logging.logging import generate_code_hash
    seed_events([edit("old.py", 1, Code="v1"), edit("old.py", 2), rename("old.py", "new.py", 3), edit("new.py", 4)])
    assert orders(get_edits("new.py", last_codestate_id=generate_code_hash("v1"))) == []


def test_unknown_codestate_id_means_no_cutoff(get_edits):
    seed_events([edit("a.py", 1), edit("a.py", 2)])
    assert orders(get_edits("a.py", last_codestate_id="no-such-id")) == [1, 2]


@pytest.mark.xfail(reason="B10: the cutoff looks up the CodeStateID across all subjects, so identical code (e.g. starter code) from another subject can set it")
def test_cutoff_only_looks_at_this_subjects_code(get_edits):
    from provena.api.logging.logging import generate_code_hash
    seed_events([edit("a.py", 1, SubjectID="s2", Code="starter"),
                 edit("a.py", 2), edit("a.py", 3), edit("a.py", 4, Code="starter")])
    assert orders(get_edits("a.py", last_codestate_id=generate_code_hash("starter"))) == [2, 3, 4]


# --- /read/edits_in_range -------------------------------------------------------

def test_edits_in_range_is_inclusive_and_covers_all_files(client):
    seed_events([edit("a.py", 1), edit("b.py", 2), edit("a.py", 3), edit("b.py", 4), edit("a.py", 5, SubjectID="s2")])
    response = client.get("/read/edits_in_range", headers=instructor_key_headers(), params={
        "subject_id": "s1", "start_client_timestamp": timestamp(2), "end_client_timestamp": timestamp(4),
    })
    assert response.status_code == 200
    assert orders(response.json()) == [2, 3, 4]
