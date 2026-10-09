"""T4: the simple listing endpoints under /read (assignments, subjects,
time ranges, sections, per-assignment submission stats)."""

import pytest

from tests.support.provena_helpers import event, instructor_key_headers, seed_events


@pytest.fixture
def get(client):
    headers = instructor_key_headers()

    def get(path, **params):
        response = client.get(path, headers=headers, params=params)
        assert response.status_code == 200, response.text
        return response.json()
    return get


def test_assignments_are_distinct_and_skip_nulls(get):
    seed_events([event("Submit", AssignmentID="A1"), event("Submit", AssignmentID="A1"),
                 event("File.Edit", AssignmentID="A2"), event("Session.Start")])
    assert sorted(get("/read/assignments")) == ["A1", "A2"]


def test_assignment_ids_differing_in_case_collapse(get):
    # Q-b: DISTINCT follows the case-insensitive collation; whichever
    # spelling MySQL sees first is returned.
    seed_events([event("Submit", AssignmentID="A1"), event("Submit", AssignmentID="a1")])
    assert len(get("/read/assignments")) == 1


def test_subjects_are_distinct_and_skip_nulls(get):
    seed_events([event("Session.Start", SubjectID="s1"), event("Session.Start", SubjectID="s1"),
                 event("Session.Start", SubjectID="s2"), event("Session.Start", SubjectID=None)])
    assert sorted(get("/read/subjects")) == ["s1", "s2"]


def test_time_range_is_min_and_max_client_timestamp(get):
    seed_events([event("Session.Start", SubjectID="s1", ClientTimestamp=ts)
                 for ts in ["2026-01-02T00:00:00", "2026-01-01T00:00:00", "2026-01-03T00:00:00"]]
                + [event("Session.Start", SubjectID="s2", ClientTimestamp="2025-01-01T00:00:00")])
    assert get("/read/subjects/s1/time_range") == {
        "MinClientTimestamp": "2026-01-01T00:00:00",
        "MaxClientTimestamp": "2026-01-03T00:00:00",
    }


def test_time_range_compares_timestamps_as_strings(get):
    # Pinned: timestamps are strings, so mixed formats/offsets sort
    # lexically, not chronologically. 10:00-05:00 is later than 12:00+00:00.
    seed_events([event("Session.Start", SubjectID="s1", ClientTimestamp="2026-01-01T10:00:00-0500"),
                 event("Session.Start", SubjectID="s1", ClientTimestamp="2026-01-01T12:00:00+0000")])
    assert get("/read/subjects/s1/time_range")["MaxClientTimestamp"] == "2026-01-01T12:00:00+0000"


def test_time_range_of_unknown_subject_is_null(get):
    assert get("/read/subjects/nobody/time_range") == {"MinClientTimestamp": None, "MaxClientTimestamp": None}


def test_codestate_sections_need_a_save(get):
    seed_events([
        event("File.Save", SubjectID="s1", CodeStateSection="proj/saved.py"),
        event("File.Save", SubjectID="s1", CodeStateSection="proj/saved.py"),
        event("File.Edit", SubjectID="s1", CodeStateSection="proj/edited_only.py"),
        event("Submit", SubjectID="s1", CodeStateSection="saved.py"),
        event("File.Save", SubjectID="s2", CodeStateSection="proj/other_subject.py"),
    ])
    assert get("/read/subjects/s1/codestate_sections") == ["proj/saved.py"]


# --- /read/assignments/{id}/subjects ------------------------------------------

def submit(subject, score, server_timestamp, assignment="A1"):
    return event("Submit", SubjectID=subject, AssignmentID=assignment, Score=score, ServerTimestamp=server_timestamp)


def test_assignment_subjects_have_last_submission_and_max_score(get):
    seed_events([
        submit("s1", 0.5, "2026-01-01T00:00:00"),
        submit("s1", 0.9, "2026-01-02T00:00:00"),
        submit("s1", 0.7, "2026-01-03T00:00:00"),
        submit("s2", 1.0, "2026-01-01T00:00:00"),
        submit("s3", 1.0, "2026-01-01T00:00:00", assignment="A2"),
        event("File.Edit", SubjectID="s4", AssignmentID="A1"),
    ])
    rows = sorted(get("/read/assignments/A1/subjects"), key=lambda r: r["SubjectID"])
    assert rows == [
        {"SubjectID": "s1", "LastSubmissionTime": "2026-01-03T00:00:00", "MaxScore": 0.9},
        {"SubjectID": "s2", "LastSubmissionTime": "2026-01-01T00:00:00", "MaxScore": 1.0},
    ]


def test_assignment_subjects_ignore_child_events_without_scores(get):
    # Multi-file submissions add Score-less child events; MAX skips them.
    seed_events([submit("s1", 0.5, "2026-01-01T00:00:00"), submit("s1", None, "2026-01-01T00:00:00")])
    assert get("/read/assignments/A1/subjects")[0]["MaxScore"] == 0.5


def test_unknown_assignment_has_no_subjects(get):
    assert get("/read/assignments/nope/subjects") == []


def test_assignment_subjects_with_no_scores(app, client):
    from fastapi.testclient import TestClient
    seed_events([submit("s1", None, "2026-01-01T00:00:00")])
    with TestClient(app, raise_server_exceptions=False) as lenient:
        response = lenient.get("/read/assignments/A1/subjects", headers=instructor_key_headers())
    assert response.status_code == 200
    assert response.json() == [{"SubjectID": "s1", "LastSubmissionTime": "2026-01-01T00:00:00", "MaxScore": None}]


# --- /read/sessions/{id}/last_synced_order ------------------------------------

def test_last_synced_order_is_the_max_order_in_the_session(client):
    seed_events([event("File.Edit", SessionID="sess", Order=n) for n in (3, 10, 7)]
                + [event("File.Edit", SessionID="other", Order=99)])
    assert client.get("/read/sessions/sess/last_synced_order").json() == 10
