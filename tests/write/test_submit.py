"""T3: POST /submit, which fans one submission out into one event per
(subject x code-state section)."""

import logging

import pytest

from provena.api.logging.logging import generate_code_hash

from tests.support.provena_helpers import instructor_key_headers, main_table_rows, submit_key_headers


def submission(subjects=("s1",), sections=(("main.py", "x = 1"),), **overrides):
    body = {
        "SubjectIDs": list(subjects),
        "AssignmentID": "A1",
        "ToolInstances": "autograder",
        "Score": 0.75,
        "ScoreDetails": "3/4 tests",
        "TermID": "F26",
        "CourseID": "CSC111",
        "CodeState": [{"CodeStateSection": path, "Code": code} for path, code in sections],
    }
    body.update(overrides)
    return body


@pytest.fixture
def submit(client):
    def submit(body):
        return client.post("/submit", json=body, headers=submit_key_headers())
    return submit


def submit_rows():
    return main_table_rows(EventType="Submit")


def test_one_subject_one_section_is_a_single_event(submit):
    response = submit(submission())

    assert response.status_code == 200
    assert response.json()["success"] is True
    [row] = submit_rows()
    assert row["SubjectID"] == "s1"
    assert row["AssignmentID"] == "A1"
    assert row["CodeStateSection"] == "main.py"
    assert row["Code"] == "x = 1"
    assert row["CodeStateID"] == generate_code_hash("x = 1")
    assert row["Score"] == 0.75
    assert row["ScoreDetails"] == "3/4 tests"
    assert (row["TermID"], row["CourseID"], row["ToolInstances"]) == ("F26", "CSC111", "autograder")
    assert row["ServerTimestamp"]
    assert "ParentEventID" not in row


def test_multiple_sections_become_a_parent_and_children(submit):
    submit(submission(sections=[("a.py", "a = 1"), ("b.py", "b = 2")]))

    rows = submit_rows()
    [parent] = [r for r in rows if "ParentEventID" not in r]
    children = sorted((r for r in rows if "ParentEventID" in r), key=lambda r: r["CodeStateSection"])

    assert "Code" not in parent and "CodeStateSection" not in parent
    assert parent["Score"] == 0.75 and parent["ScoreDetails"] == "3/4 tests"
    assert [c["CodeStateSection"] for c in children] == ["a.py", "b.py"]
    assert [c["CodeStateID"] for c in children] == [generate_code_hash("a = 1"), generate_code_hash("b = 2")]
    for child in children:
        assert child["ParentEventID"] == parent["EventID"]
        assert "Score" not in child and "ScoreDetails" not in child
        assert child["AssignmentID"] == "A1"
    assert len({r["ServerTimestamp"] for r in rows}) == 1


def test_multiple_subjects_each_get_the_full_set(submit):
    submit(submission(subjects=["s1", "s2", "s3"], sections=[("a.py", "a"), ("b.py", "b")]))
    rows = submit_rows()
    assert len(rows) == 3 * (1 + 2)
    for subject in ["s1", "s2", "s3"]:
        mine = [r for r in rows if r["SubjectID"] == subject]
        assert sorted(r.get("CodeStateSection", "") for r in mine) == ["", "a.py", "b.py"]


def test_event_ids_are_unique_with_multiple_subjects(submit):
    submit(submission(subjects=["s1", "s2"], sections=[("a.py", "a"), ("b.py", "b")]))
    rows = submit_rows()
    assert len({r["EventID"] for r in rows}) == len(rows)


def test_children_point_at_their_own_subjects_parent(submit):
    submit(submission(subjects=["s1", "s2"], sections=[("a.py", "a"), ("b.py", "b")]))
    rows = submit_rows()
    for subject in ["s1", "s2"]:
        parents = [r for r in rows if r["SubjectID"] == subject and "ParentEventID" not in r]
        children = [r for r in rows if r["SubjectID"] == subject and "ParentEventID" in r]
        [parent] = parents
        assert all(c["ParentEventID"] == parent["EventID"] for c in children)
        others = [r for r in rows if r["SubjectID"] != subject and "ParentEventID" not in r]
        assert all(o["EventID"] != parent["EventID"] for o in others)


def test_no_subjects_is_rejected(submit):
    assert submit(submission(subjects=[])).status_code == 422
    assert main_table_rows() == []


def test_no_code_state_stores_just_the_parent(submit):
    # D14: allowed -- some projects have no files (the autograder then
    # sends e.g. Score 0).
    response = submit(submission(sections=[], Score=0.0))
    assert response.status_code == 200
    [row] = submit_rows()
    assert "Code" not in row and "CodeStateSection" not in row
    assert row["Score"] == 0.0


def test_empty_submission_works_with_the_read_side(client, submit):
    """D14: check that a file-less Submit doesn't break the code reading
    Submit events."""
    submit(submission(sections=[], Score=0.0))
    headers = instructor_key_headers()

    stats = client.get("/read/assignments/A1/subjects", headers=headers)
    assert stats.status_code == 200
    assert [(r["SubjectID"], r["MaxScore"]) for r in stats.json()] == [("s1", 0.0)]

    assert client.post("/read/update_mapping_table", headers=headers).status_code == 200
    sections = client.get("/read/assignments/A1/s1/code_state_sections", headers=headers)
    assert sections.json() == []

    count = client.post("/get_event_count", headers=headers, json={"SubjectIDs": ["s1"], "CodeState": []})
    assert count.json() == 0


@pytest.mark.parametrize("field", ["Score", "ScoreDetails", "TermID", "CourseID"])
def test_null_optional_fields_are_accepted(submit, field):
    assert submit(submission(**{field: None})).status_code == 200


@pytest.mark.xfail(reason="B23 (D15): Optional[...] without a default is a required key in pydantic v2, so omitting it is a 422")
@pytest.mark.parametrize("field", ["Score", "ScoreDetails", "TermID", "CourseID"])
def test_optional_fields_can_be_omitted(submit, field):
    body = submission()
    del body[field]
    assert submit(body).status_code == 200
    assert field not in submit_rows()[0]


def test_submit_does_not_go_through_the_events_fallback(submit):
    # /submit has no malformed-event fallback: a DB failure just comes back
    # as success: false.
    response = submit(submission(subjects=["s" * 300]))
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert main_table_rows() == []


@pytest.mark.xfail(raises=TypeError, reason="B5: log_submit's logger.info passes an argument with no placeholder")
def test_submit_logs_cleanly_at_info(submit, caplog):
    with caplog.at_level(logging.INFO, logger="provena.api.logging.logging"):
        submit(submission())
    for record in caplog.records:
        record.getMessage()
