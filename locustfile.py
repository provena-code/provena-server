
import random
import string
import uuid
from datetime import datetime
import json
from locust import HttpUser, task, between

# --- Data Generation Helpers ---

# Load OpenAPI spec to get enum values
with open('openapi.json', 'r') as f:
    openapi_spec = json.load(f)

# Helper to extract enum values from the OpenAPI spec
def get_enum_values(schema_name: str):
    return openapi_spec["components"]["schemas"][schema_name]["enum"]

EVENT_TYPES = get_enum_values("EventType")
EDIT_TYPES = get_enum_values("EditType")
EVENT_INITIATORS = get_enum_values("EventInitiator")

# Filter out event types that might prematurely end the session for general logging
GENERAL_EVENT_TYPES = [et for et in EVENT_TYPES if et not in ["Session.End", "Project.Close"]]


def random_string(length: int = 10) -> str:
    """Generate a random string of fixed length."""
    letters = string.ascii_lowercase
    return ''.join(random.choice(letters) for i in range(length))

def generate_main_table_event(session_id: str, order: int, subject_id: str, code_state_section: str) -> dict:
    """Generates a single MainTableEvent with random data."""
    event = {
        "EventType": random.choice(GENERAL_EVENT_TYPES),
        "EventID": str(uuid.uuid4()),
        "ToolInstances": "locust-test",
        "SessionID": session_id,
        "Order": order,
        "SubjectID": subject_id,
        "CodeStateSection": code_state_section,
        "ClientTimestamp": datetime.now().isoformat(),
    }

    # Add optional fields with ~50% probability
    if random.random() < 0.5:
        event["ParentEventID"] = str(uuid.uuid4())
    if random.random() < 0.5:
        event["CourseID"] = "course-" + random_string(5)
    if random.random() < 0.5:
        event["TermID"] = "term-" + random_string(5)
    if random.random() < 0.5:
        event["AssignmentID"] = "assign-" + random_string(5)
    if random.random() < 0.5:
        event["ProjectID"] = "proj-" + random_string(5)
    if random.random() < 0.5:
        event["EventInitiator"] = random.choice(EVENT_INITIATORS)
    if random.random() < 0.5:
        event["EditType"] = random.choice(EDIT_TYPES)
        event["InsertText"] = random_string(20)
        event["DeleteText"] = random_string(10)

    # Add 'Code' field for ~1 in 20 events
    if random.random() < 0.05: # 1 in 20
        event["Code"] = random_string(random.randint(1, 1000))
    else:
        event["Code"] = ""

    return event

# --- Locust Task Definitions ---

class WebsiteUser(HttpUser):
    wait_time = between(2.0, 4.0)
    host = "http://127.0.0.1:8001"

    def on_start(self):
        """Called when a simulated user starts. Simulates the initial log sync."""
        self.session_id = str(uuid.uuid4())
        self.order = 0
        self.known_subject_ids = ["file://" + random_string(15) + ".py"] # Single SubjectID per user
        self.known_code_state_sections = self.known_subject_ids.copy()

        # Sync existing logs, 0-20 rounds
        num_sync_rounds = random.randint(0, 20)
        for _ in range(num_sync_rounds):
            # Check last synced order (will likely be -1 for a new session)
            self.client.get(f"/read/sessions/{self.session_id}/last_synced_order", name="/read/sessions/{session_id}/last_synced_order")

            # Post a "large prior log"
            # "large prior log" is 0-500 events
            num_events = random.randint(0, 500)
            if num_events > 0:
                events_to_send = []
                for _ in range(num_events):
                    subject_id = random.choice(self.known_subject_ids)
                    code_state_section = random.choice(self.known_code_state_sections)
                    event = generate_main_table_event(self.session_id, self.order, subject_id, code_state_section)
                    events_to_send.append(event)
                    self.order += 1

                self.client.post("/events", json=events_to_send)

    @task(20)
    def log_events(self):
        """Simulates a user doing work and logging events."""
        num_events = random.randint(1, 20)
        events_to_send = []
        for _ in range(num_events):
            subject_id = random.choice(self.known_subject_ids)
            code_state_section = random.choice(self.known_code_state_sections)
            event = generate_main_table_event(self.session_id, self.order, subject_id, code_state_section)
            events_to_send.append(event)
            self.order += 1

        self.client.post("/events", json=events_to_send)

    @task(1)
    def submit_and_get_count(self):
        """Simulates the submission process."""
        # Submissions happen throughout, and usually in small bursts (e.g. 1-5 attempts)
        for _ in range(random.randint(1, 5)):
            num_sections = random.randint(1, 4)
            # Use real CodeStateSection values from the user's history
            sections_to_use = random.sample(self.known_code_state_sections, k=min(num_sections, len(self.known_code_state_sections)))

            code_state = []
            for section in sections_to_use:
                code_state.append({
                    "CodeStateSection": section,
                    "Code": random_string(random.randint(0, 1000))
                })

            submission_info = {
                "SubjectIDs": self.known_subject_ids, # Use the single SubjectID
                "CodeState": code_state
            }

            # 1. Get event count
            self.client.post("/get_event_count", json=submission_info)

            # 2. Post to /submit
            submit_event = submission_info.copy()
            submit_event.update({
                "AssignmentID": "assign-" + random_string(5),
                "ToolInstances": "locust-submit-test",
                "Score": random.random(),
                "ScoreDetails": random_string(1000),
                "CourseID": "course-" + random_string(5),
                "TermID": "term-" + random_string(5)
            })
            self.client.post("/submit", json=submit_event)

    @task(1)
    def send_malformed_event(self):
        """Sends a deliberately malformed event to test error handling."""
        # Malformed requests should aim to trigger RequestValidationError
        # and should return 200 with LogResult indicating errors.

        malform_type = random.choice(["missing_field", "invalid_json"])

        if malform_type == "missing_field":
            subject_id = random.choice(self.known_subject_ids)
            code_state_section = random.choice(self.known_code_state_sections)
            event = generate_main_table_event(self.session_id, self.order, subject_id, code_state_section)
            self.order += 1

            # Remove a required field
            del event["EventType"]

            self.client.post("/events", json=[event], name="/events [malformed-missing-field]")
        elif malform_type == "invalid_json":
            # Send entirely invalid JSON
            self.client.post("/events", data="this is not json", headers={"Content-Type": "application/json"}, name="/events [malformed-invalid-json]")

