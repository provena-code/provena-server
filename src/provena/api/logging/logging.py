from fastapi import APIRouter, Depends
from typing import List, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from progsnap2.api.config import PS2APIConfig
from progsnap2.api.models import TempCodeStateEntry
from progsnap2.database.writer.sql_writer import SQLWriter
from progsnap2.api.events import DataModelGenerator
from progsnap2.database.writer.db_writer import DBWriter, LogResult
from progsnap2.database.writer.db_writer_factory import IOFactory, SQLIOFactory
from progsnap2.spec.enums import CoreTables
from progsnap2.spec.spec_definition import PS2Versions, ProgSnap2Spec
from progsnap2.spec.gen.gen_client import generate_ts_methods
from progsnap2.spec.enums import MainTableColumns as Cols

from provena.configs import api_config, spec, MainTableEvent

db_writer_factory: SQLIOFactory = IOFactory.create_factory(api_config.database_config, ps2_spec=spec)

# TODO: Don't actually do this automatically every time...
with db_writer_factory.create_writer() as writer:
    # Create the tables in the database
    writer.initialize_database()
    # writer.update_database()

# For use in Depends
def create_writer():
    with db_writer_factory.create_writer() as writer:
        yield writer


# TODO: Should probably have a prefix
router = APIRouter()

@router.post("/events", operation_id="addEvents", response_model=LogResult)
def add_events_with_code_states(events: List[MainTableEvent], writer: SQLWriter = Depends(create_writer)): # type: ignore
    """
    Add events and code states to the database at the same time to ensure consistency.

    Note: TempCodeState.code_state_id is a temporary ID that will be remapped when logging
    the events. It is used to map multiple events to the same code state in this request.
    """
    events = [event.model_dump(exclude_none=True) for event in events]
    if api_config.add_server_timestamps:
        writer.add_server_timestamps(events)

    return writer.add_events(events)

class CodeStateSection(BaseModel):
    CodeStateSection: str
    Code: str

class SubmitEvent(BaseModel):
    EventType: Literal["Submit"]
    SubjectIDs: List[str] = Field(..., min_items=1)
    AssignmentID: str
    CodeState: List[CodeStateSection]
    Score: float
    ToolInstances: str
    ScoreDetails: str | None
    TermID: str | None
    CourseID: str | None

@router.post("/submit", operation_id="submit", response_model=LogResult)
def log_submit(event: SubmitEvent, writer: SQLWriter = Depends(create_writer)): # type: ignore
    """
    Submit an event to the database.
    """
    base_event = event.model_dump(exclude_none=True)
    codestate_sections = base_event["CodeState"]
    subjects = base_event["SubjectIDs"]
    if api_config.add_server_timestamps:
        writer.add_server_timestamps([base_event])

    del base_event["CodeState"]
    del base_event["SubjectIDs"]
    parent_event = base_event.copy()
    parent_event[Cols.EventID] = writer.generate_event_id()
    subjectless_events = [parent_event]
    if len(codestate_sections) == 1:
        parent_event[Cols.Code] = codestate_sections[0]["Code"]
        parent_event[Cols.CodeStateSection] = codestate_sections[0]["CodeStateSection"]
    else:
        for section in codestate_sections:
            sub_event = base_event.copy()
            sub_event[Cols.CodeStateSection] = section["CodeStateSection"]
            sub_event[Cols.Code] = section["Code"]
            sub_event[Cols.ParentEventID] = parent_event[Cols.EventID]
            sub_event[Cols.Score] = None
            sub_event[Cols.ScoreDetails] = None
            subjectless_events.append(sub_event)

    if len(subjects) == 1:
        subjectless_events[0][Cols.SubjectID] = subjects[0]
        events = subjectless_events
    else:
        events = []
        for subject in subjects:
            for sub_event in subjectless_events:
                new_event = sub_event.copy()
                new_event[Cols.SubjectID] = subject
                events.append(new_event)

    print(f"Logging {len(events)} events", events)
    return writer.add_events(events)

@router.post("/submit_and_count", operation_id="submitAndGetCount")
def log_submit_and_get_count(event: SubmitEvent, writer: SQLWriter = Depends(create_writer)): # type: ignore
    try:
        result = log_submit(event, writer)
        if (not result.success):
            print("Error logging event:", result.errors)
    except Exception as e:
        print(f"Error logging event: {e}")
        pass

    count = get_event_count(event, writer)
    return count

def get_event_count(event: SubmitEvent, writer: SQLWriter = Depends(create_writer)): # type: ignore
    manager = writer.context.table_manager
    codestates_table = manager.get_table(CoreTables.CodeStates)
    main_table = manager.get_table(CoreTables.MainTable)
    codestate_sections = [section.CodeStateSection for section in event.CodeState]
    # TODO: Also confirm that the code being submitted has logs
    statement = select(func.count()).where(
        main_table.c.SubjectID.in_(event.SubjectIDs),
        main_table.c.CodeStateSection.in_(codestate_sections)
    )
    result = writer.conn.execute(statement).scalar()
    return result


# @router.get("/generate_api_helper", operation_id="generateAPIHelper", response_class=PlainTextResponse)
# def generate_api_helper() -> str:
#     return generate_ts_methods(spec)


# I don't think this is needed (or the whole type), but I'll keep for now
# @router.get("/placeholder")
# def get_additional_column_types(additionalColumns: AnyAdditionalColumns): # type: ignore
#     """
#     Placeholder endpoint to get the additional column types.
#     """
#     pass