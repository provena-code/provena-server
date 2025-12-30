Read over the API described in:
* @openapi.json
* @src/provena/api/logging/logging.py
* @src/provena/api/read/sessions.py

Then write locust.py to test them as described below.

Make the host configurable, but default to http://127.0.0.1:8001/

# User Workflow

A typical user experience is:
* When starting a session, sync existing logs, which consists of multiple rounds of:
  * Check the /read/sessions/{session_id}/last_synced_order endpoint for a prior `session_id`
  * Post a large prior log to the /events endpoint
* Do work, generating more events which get posted every 3 seconds to the /events endpoint in batches of 1-20 events.
* Occasionally, test sending an improperly formatted request to `/events`
* Occasionally submit 1-5 times which:
  * Gets /get_event_count in @src/provena/api/logging.py
  * Posts to /submit in @src/provena/api/logging.py

This should go on for the whole test.

# Endpoints

Here are all the endpoints and details about what goes in them (see @openapi.json for details):

## /read/sessions/{session_id}/last_synced_order
File: @src/provena/api/read/sessions.py
Function: get_last_synced_log_index

Input: The `session_id` should be a `SessionID` from a previously logged session, or just any random ID.

Expected output: Should be a number (don't need to validate it here).


## /events
File: @src/provena/api/logging/logging.py
Function: add_events_with_code_states

A list of MainTableEvent objects (see @openapi.json). Most fields can be short and random (with proper values for enums, dates, etc.) but:
* Keep track of `SessionID`, which should match future calls to `/read/sessions/{session_id}/last_synced_order`
* Keep track of `CodeStateSection`, which should match the CodeStateSections in future submissions to `/submit` and `/get_event_count`
* `Code` should be empty for most requests, but for about 1 in 20 it should be a 1-1000 characters random string.
* Required fields (EventType, EventID, ToolInstances) should be present, and optional fields will have a value ~50% of the time.

For the number of events to include in the list, see User Workflow.

Expected Output: a LogResult. Assuming you formatting things correctly, it should have `Success == true` with no errors (there may be warnings since you're generating things randomly; that's ok).

## /submit
File: @src/provena/api/logging/logging.py
Function: log_submit

Input: See SubmitEvent in logging.py. Most fields are short and can be any value, but ScoreDetails should be a longer text blob (e.g. ~1000 characters), and CodeStateSection.Code should be a longer text blob (0-1000 characters). CodeState should include 1-4 CodeStateSections. Ideally, use real CodeStateSection values from earlier logged events.

Output: None

## /get_event_count
File: @src/provena/api/logging/logging.py
Function: get_event_count

Input: See SubmissionInfo in logging.py. Parameters are the exact same as for /submit (but only fields in SubmissionInfo are sent).

Expected Output: Should be a number (don't need to verify).


# Testing
At peak volume, I expect there will be up to 100 users working and logging events regularly.

# Q&A
