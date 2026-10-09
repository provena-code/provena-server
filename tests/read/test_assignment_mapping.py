"""
T4: the assignment map (provena.api.read.logic.mapping): which real files
each subject submitted for each assignment. Updated on every call to
/read/assignments/{a}/{s}/code_state_sections and /read/update_mapping_table.

Submissions are matched to the subject's own logged events by CodeStateID,
plus a path check: the logged path must equal the submitted name or end
with "/" + it. ServerTimestamps are set explicitly so ordering is
deterministic.
"""

import pytest
from sqlalchemy import select

from provena.api.logging.logging import generate_code_hash
from provena.assignments.models import AssignmentMapping
from provena.db.base import SessionLocal

from tests.support.provena_helpers import event, instructor_key_headers, seed_events


def save(path, code, subject="s1"):
    return event("File.Save", SubjectID=subject, CodeStateSection=path, Code=code)


def submit(name, code, server_timestamp, subject="s1", assignment="A1"):
    return event("Submit", SubjectID=subject, AssignmentID=assignment, CodeStateSection=name, Code=code,
                 ServerTimestamp=server_timestamp, ClientTimestamp=None)


T1, T2, T3 = "2026-02-01T10:00:00", "2026-02-02T10:00:00", "2026-02-03T10:00:00"


@pytest.fixture
def sections(client):
    headers = instructor_key_headers()

    def sections(subject="s1", assignment="A1"):
        response = client.get(f"/read/assignments/{assignment}/{subject}/code_state_sections", headers=headers)
        assert response.status_code == 200, response.text
        return sorted(response.json())
    return sections


@pytest.fixture
def update(client):
    def update():
        response = client.post("/read/update_mapping_table", headers=instructor_key_headers())
        assert response.status_code == 200, response.text
        return response.json()
    return update


def mapping_rows():
    with SessionLocal() as db:
        return [
            (m.SubjectID, m.AssignmentID, m.CodeStateSection, m.LastValidTimestamp, m.CodeStateID)
            for m in db.execute(select(AssignmentMapping).order_by(AssignmentMapping.CodeStateSection)).scalars()
        ]


def test_submitted_file_maps_to_its_logged_path(sections):
    seed_events([save("proj/hw1/main.py", "v1"), submit("main.py", "v1", T1)])
    assert sections() == ["proj/hw1/main.py"]
    assert mapping_rows() == [("s1", "A1", "proj/hw1/main.py", T1, generate_code_hash("v1"))]


def test_update_endpoint_returns_nothing(update):
    assert update() is None


def test_same_code_under_a_different_name_does_not_map(sections):
    seed_events([save("proj/other.py", "v1"), submit("main.py", "v1", T1)])
    assert sections() == []


def test_name_must_match_a_whole_path_component(sections):
    seed_events([save("proj/xmain.py", "v1"), submit("main.py", "v1", T1)])
    assert sections() == []


def test_only_the_subjects_own_files_map(sections):
    seed_events([save("proj/main.py", "v1", subject="s2"), submit("main.py", "v1", T1)])
    assert sections() == []


def test_multi_file_submission_maps_every_file(sections):
    seed_events([save("proj/a.py", "a"), save("proj/b.py", "b"),
                 submit("a.py", "a", T1), submit("b.py", "b", T1)])
    assert sections() == ["proj/a.py", "proj/b.py"]


def test_only_the_latest_submission_maps(sections):
    # D16: only the latest (graded) submission per subject and assignment
    # is mapped.
    seed_events([save("proj/a.py", "a"), save("proj/b.py", "b"),
                 submit("a.py", "a", T1), submit("b.py", "b", T2)])
    assert sections() == ["proj/b.py"]


def test_a_file_can_map_to_several_assignments(sections):
    seed_events([save("proj/util.py", "u"), submit("util.py", "u", T1, assignment="A1"),
                 submit("util.py", "u", T1, assignment="A2")])
    assert sections(assignment="A1") == ["proj/util.py"]
    assert sections(assignment="A2") == ["proj/util.py"]


def test_repeated_updates_add_nothing(sections, update):
    seed_events([save("proj/main.py", "v1"), submit("main.py", "v1", T1)])
    update()
    before = mapping_rows()
    update()
    update()
    assert mapping_rows() == before


def test_resubmission_moves_last_valid_timestamp(update):
    seed_events([save("proj/main.py", "v1"), submit("main.py", "v1", T1)])
    update()
    seed_events([save("proj/main.py", "v2"), submit("main.py", "v2", T2)])
    update()
    [(_, _, _, last_valid, _)] = mapping_rows()
    assert last_valid == T2


@pytest.mark.xfail(reason="B9: the upsert only updates LastValidTimestamp, so CodeStateID keeps the first submission's code")
def test_resubmission_updates_the_codestate_id(update):
    seed_events([save("proj/main.py", "v1"), submit("main.py", "v1", T1)])
    update()
    seed_events([save("proj/main.py", "v2"), submit("main.py", "v2", T2)])
    update()
    [(_, _, _, _, codestate_id)] = mapping_rows()
    assert codestate_id == generate_code_hash("v2")


def test_resubmitting_a_different_file_keeps_the_old_mapping(sections, update):
    # D16: accepted quirk. Updates are incremental and never delete, so a
    # file mapped by an earlier update stays mapped after a later
    # submission of different files -- unlike a single update over both
    # (test_only_the_latest_submission_maps). Deleting stale rows would make
    # files vanish from an assignment's history after a resubmission.
    seed_events([save("proj/a.py", "a"), submit("a.py", "a", T1)])
    update()
    seed_events([save("proj/b.py", "b"), submit("b.py", "b", T2)])
    assert sections() == ["proj/a.py", "proj/b.py"]


@pytest.mark.xfail(reason="B17: an update only looks at submissions newer than the newest mapping, so logs that sync after a later submission was mapped are never matched")
def test_logs_that_arrive_late_still_map(update, sections):
    seed_events([submit("main.py", "v1", T1)])                    # s1 submits; logs not synced yet
    seed_events([save("proj/x.py", "x", subject="s2"), submit("x.py", "x", T2, subject="s2")])
    update()                                                      # maps s2 at T2
    seed_events([save("proj/main.py", "v1")])                     # s1's logs arrive
    assert sections() == ["proj/main.py"]


def test_paths_differing_in_case_collapse_into_one_mapping(sections):
    # Q-b: the mapping query's DISTINCT follows the case-insensitive
    # collation, so case variants collapse before they're inserted. D8's
    # exact (case-sensitive) hash key therefore doesn't change behavior on
    # this path; it only matters for direct inserts (tests/test_app_tables.py).
    seed_events([save("proj/Main.py", "v1"), save("proj/main.py", "v1"), submit("main.py", "v1", T1)])
    assert len(sections()) == 1
    assert len(mapping_rows()) == 1
