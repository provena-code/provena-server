"""T3: POST /events, including its error-logging fallbacks."""

import pytest

from provena.api.logging.logging import generate_code_hash

from tests.support.provena_helpers import (
    event, logging_error_rows, main_table_rows, student_headers,
)


@pytest.fixture
def post(client):
    headers = student_headers()

    def post(body, **kwargs):
        return client.post("/events", json=body, headers=headers, **kwargs)
    return post


# --- Happy path ---------------------------------------------------------------

def test_events_are_stored_with_server_timestamps_and_codestate_ids(post):
    events = [
        event("File.Edit", Order=1, Code="x = 1\r\n", EditType="Insert", InsertText="1"),
        event("Session.Start", Order=2),
    ]

    response = post(events)

    assert response.status_code == 200
    assert response.json() == {"success": True, "warnings": [], "errors": []}
    edit, start = main_table_rows()
    assert edit["EventID"] == events[0]["EventID"]
    assert edit["EditType"] == "Insert"
    assert edit["CodeStateID"] == generate_code_hash("x = 1\n")
    assert edit["Code"] == "x = 1\r\n"  # stored as sent; only the hash is canonical
    assert "CodeStateID" not in start
    assert edit["ServerTimestamp"] and start["ServerTimestamp"]


def test_empty_batch_succeeds(post):
    assert post([]).json()["success"] is True
    assert main_table_rows() == []


def test_events_with_different_columns_in_one_batch(post):
    response = post([
        event("File.Rename", Order=1, CodeStateSection="a.py", DestinationCodeStateSection="b.py"),
        event("Project.Open", Order=2, ProjectID="p1"),
    ])
    assert response.json()["success"] is True
    rename, project = main_table_rows()
    assert rename["DestinationCodeStateSection"] == "b.py" and "ProjectID" not in rename
    assert project["ProjectID"] == "p1" and "DestinationCodeStateSection" not in project


def test_unknown_fields_are_silently_dropped(post):
    # [inferred] The generated model ignores extra fields.
    assert post([event("Session.Start", NotAColumn="x")]).json()["success"] is True
    assert "NotAColumn" not in main_table_rows()[0]


def test_missing_event_specific_columns_are_stored_with_a_warning(post):
    # [inferred] Event-type requirements (File.Edit needs CodeStateSection)
    # are warnings, not rejections.
    response = post([event("File.Edit", CodeStateSection=None)])
    body = response.json()
    assert body["success"] is True
    assert body["warnings"]
    assert len(main_table_rows()) == 1


@pytest.mark.xfail(reason="B21 (D12): a client-supplied ServerTimestamp is kept instead of overwritten")
def test_client_server_timestamp_is_overwritten_with_a_warning(post):
    response = post([event("Session.Start", ServerTimestamp="1999-01-01T00:00:00")])
    assert main_table_rows()[0]["ServerTimestamp"] != "1999-01-01T00:00:00"
    assert any("ServerTimestamp" in warning for warning in response.json()["warnings"])


def test_subject_id_is_not_tied_to_the_login(post):
    # Q-c (documented non-goal): any authenticated student can log events
    # for any SubjectID.
    post([event("Session.Start", SubjectID="someone-else")])
    assert main_table_rows()[0]["SubjectID"] == "someone-else"


# --- Duplicate EventIDs (D13) -------------------------------------------------
# EventID should be unique. A duplicate that matches the stored event
# exactly (ignoring server-assigned columns) is a retry: skip it, with a
# warning. A duplicate that differs is real data: store it under a new
# EventID, with a warning. Nothing is ever dropped unless it's an exact match.

def _warned_about_duplicates(response) -> bool:
    return any("EventID" in warning for warning in response.json()["warnings"])


@pytest.mark.xfail(reason="B22 (D13): duplicate EventIDs aren't detected; a retried batch is stored twice")
def test_retried_batch_is_stored_once_with_a_warning(post):
    batch = [event("Session.Start", Order=1), event("File.Edit", Order=2, Code="x", EditType="Insert")]
    post(batch)
    response = post(batch)  # new ServerTimestamps; otherwise identical

    assert response.json()["success"] is True
    assert _warned_about_duplicates(response)
    assert len(main_table_rows()) == 2


@pytest.mark.xfail(reason="B22 (D13): duplicate EventIDs aren't detected")
def test_retry_with_explicit_nulls_is_still_a_match(post):
    # The model drops None before storing, so an explicit null and a
    # missing key must compare equal.
    original = event("Session.Start")
    post([original])
    post([{**original, "AssignmentID": None}])
    assert len(main_table_rows()) == 1


@pytest.mark.xfail(reason="B22 (D13): duplicate EventIDs aren't detected")
def test_different_event_with_a_reused_event_id_gets_a_new_id(post):
    original = event("Session.Start", Order=1)
    post([original])
    response = post([{**original, "Order": 2}])

    assert _warned_about_duplicates(response)
    rows = main_table_rows()
    assert [r["Order"] for r in rows] == [1, 2]
    assert rows[0]["EventID"] == original["EventID"]
    assert rows[1]["EventID"] != original["EventID"]


def test_difference_only_in_case_is_not_a_match(post):
    # Passes today (nothing is deduplicated yet); guards the B22 fix. A
    # false "match" would silently drop data, so the comparison must be
    # exact, not under MySQL's case/accent-insensitive collation.
    original = event("File.Edit", Code="x = 1", EditType="Insert", InsertText="a")
    post([original])
    post([{**original, "InsertText": "A"}])
    assert sorted(r["InsertText"] for r in main_table_rows()) == ["A", "a"]


@pytest.mark.xfail(reason="B22 (D13): duplicate EventIDs aren't detected")
def test_duplicates_within_one_batch(post):
    same = event("Session.Start", Order=1)
    reused = {**same, "Order": 2}
    post([same, dict(same), reused])
    rows = main_table_rows()
    assert [r["Order"] for r in rows] == [1, 2]
    assert len({r["EventID"] for r in rows}) == 2


# --- Rejected by the database -------------------------------------------------

def test_over_length_value_is_logged_as_an_error_event(post):
    """STRICT mode rejects the insert, and the fallback can't fix it either,
    so a LoggingError event is stored instead of the original."""
    response = post([event("Session.Start", SubjectID="s" * 300)])  # VARCHAR(255)

    assert response.status_code == 200
    assert any("Error inserting events" in e for e in response.json()["errors"])
    [row] = main_table_rows()
    assert row["EventType"] == "LoggingError"
    assert row["LoggingErrorID"]


def test_over_length_value_error_details_are_recorded(post):
    too_long = "s" * 300
    response = post([event("Session.Start", SubjectID=too_long)])

    # Q-a: success is True because the *error* was logged successfully.
    assert response.json()["success"] is True
    [row] = main_table_rows()
    [error] = logging_error_rows()
    assert error["LoggingErrorID"] == row["LoggingErrorID"]
    assert too_long in error["RequestBody"]


def test_one_bad_event_takes_the_whole_batch_to_the_fallback(post):
    # The batch is one INSERT, so a single bad event fails it; the fallback
    # then re-inserts events one at a time, so the good ones survive.
    good = event("Session.Start", Order=1)
    bad = event("Session.Start", Order=2, SubjectID="s" * 300)
    response = post([good, bad])

    assert response.status_code == 200
    rows = main_table_rows(order_by="EventType")
    assert [r["EventType"] for r in rows] == ["LoggingError", "Session.Start"]
    assert rows[1]["EventID"] == good["EventID"]


# --- Rejected by validation ---------------------------------------------------

def test_missing_required_field_is_logged_with_placeholders(post):
    """The validation handler stores what it can, filling required columns
    with "MISSING" (via add_malformatted_events)."""
    bad = event("Session.Start", ToolInstances=None)

    response = post([bad])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["errors"][0].startswith("Some events were malformatted")
    [row] = main_table_rows()
    assert row["EventID"] == bad["EventID"]
    assert row["ToolInstances"] == "MISSING"


def test_invalid_enum_value_is_stored_anyway(post):
    # Pinned: the malformed-event fallback stores the raw value, since the
    # DB column is a plain string.
    response = post([event("File.Edit", EditType="NotAnEditType")])
    assert response.status_code == 200
    assert main_table_rows()[0]["EditType"] == "NotAnEditType"


def test_valid_events_in_a_partly_invalid_batch_are_stored(post):
    good = event("Session.Start", Order=1)
    bad = event("Session.Start", Order=2, EventType=None)
    post([good, bad])
    rows = {r["EventID"]: r for r in main_table_rows()}
    assert rows[good["EventID"]]["EventType"] == "Session.Start"
    assert rows[bad["EventID"]]["EventType"] == "MISSING"


def test_non_json_body_is_logged_as_an_error_event(client):
    response = client.post("/events", content=b"this is {not json", headers={
        **student_headers(), "Content-Type": "application/json",
    })
    assert response.status_code == 200, response.text
    assert response.json()["errors"][0].startswith("Content was not valid JSON")
    assert [r["EventType"] for r in main_table_rows()] == ["LoggingError"]


def test_non_json_body_is_recorded_verbatim(client):
    client.post("/events", content=b"this is {not json", headers={
        **student_headers(), "Content-Type": "application/json",
    })
    [error] = logging_error_rows()
    assert error["RequestBody"] == "this is {not json"


def test_non_list_body_is_logged_as_an_error_event(post):
    response = post({"EventType": "Session.Start"})
    assert response.status_code == 200, response.text
    assert [r["EventType"] for r in main_table_rows()] == ["LoggingError"]


def test_non_list_body_is_recorded(post):
    post({"EventType": "Session.Start"})
    [error] = logging_error_rows()
    assert "Session.Start" in error["RequestBody"]


def test_validation_errors_on_other_routes_are_plain_422s(client):
    response = client.post("/get_event_count", json={"SubjectIDs": "not-a-list"}, headers=student_headers())
    assert response.status_code == 422
    assert logging_error_rows() == []


@pytest.mark.xfail(reason="B15: a body that isn't valid JSON fails before the auth dependency runs, so the validation handler logs it anonymously")
def test_unauthenticated_garbage_is_not_logged(client):
    response = client.post("/events", content=b"junk", headers={"Content-Type": "application/json"})
    assert response.status_code == 401
    assert main_table_rows() == []


def test_unauthenticated_invalid_events_are_not_stored(client):
    # Valid JSON is only checked against the model after the auth
    # dependency, so B15 doesn't reach the malformed-event fallback.
    events = [event("Session.Start", SubjectID="victim", ToolInstances=None)]
    response = client.post("/events", json=events)
    assert response.status_code == 401
    assert main_table_rows() == []


def test_unauthenticated_valid_events_are_rejected(client):
    response = client.post("/events", json=[event("Session.Start")])
    assert response.status_code == 401
    assert main_table_rows() == []
