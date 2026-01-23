import logging
logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends
from typing import List, Literal, Optional

import unicodedata
import hashlib

from pydantic import BaseModel, Field
from sqlalchemy import Column, Table, func, or_, select
from sqlalchemy.orm import Session

from progsnap2.api.config import PS2APIConfig
from progsnap2.api.models import TempCodeStateEntry
from progsnap2.database.writer.sql_writer import SQLWriter
from progsnap2.api.events import DataModelGenerator
from progsnap2.database.writer.db_writer import DBWriter, LogResult
from progsnap2.database.writer.db_writer_factory import IOFactory, SQLIOFactory
from progsnap2.spec.enums import CoreTables
from progsnap2.spec.spec_definition import PS2Versions, ProgSnap2Spec, Requirement
from progsnap2.spec.gen.gen_client import generate_ts_methods
from progsnap2.spec.enums import MainTableColumns as Cols, EventType

from provena.configs import api_config, spec, MainTableEvent

db_writer_factory: SQLIOFactory = IOFactory.create_factory(api_config.database_config, ps2_spec=spec)

# Useful for testing performance without DB
# from sqlalchemy import event
# @event.listens_for(db_writer_factory.engine, "before_cursor_execute")
# def noop_execute(conn, cursor, statement, params, context, executemany):
#     raise RuntimeError("DB disabled")

# TODO: Don't actually do this automatically every time...
with db_writer_factory.create_writer() as writer:
    # Create the tables in the database
    try:
        writer.initialize_database()
        # writer.update_database()
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

# For use in Depends
def create_writer():
    with db_writer_factory.create_writer() as writer:
        yield writer


# TODO: Should probably have a prefix
# No authentication yet, since it's just event counts and
# adding data.
# Eventually logging events will require a user-level token, and
# submit/event_count will require an instructor-level token,
# hardcoded into gradescope.
router = APIRouter()


def get_canonical_string(raw_string):
    # Strip BOM
    if raw_string.startswith('\ufeff'):
        raw_string = raw_string[1:]

    # Normalize Newlines & Unicode
    normalized = raw_string.replace('\r\n', '\n')
    normalized = unicodedata.normalize('NFC', normalized)

    return normalized

def generate_code_hash(code: str, canonicalize: bool = True) -> str:
    if canonicalize:
        code = get_canonical_string(code)
    # Only strip in the hash, since whitespace is meaningful
    normalized = code.strip()
    # MD5 should be sufficient for code hashing
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()

def add_codestate_ids(events: List[dict]) -> None:
    for event in events:
        if Cols.Code in event:
            code  = get_canonical_string(event[Cols.Code])
            event[Cols.Code] = code
            event[Cols.CodeStateID] = generate_code_hash(code, False)

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

    add_codestate_ids(events)

    result = writer.add_events(events)
    if result.success:
        return result

    try:
        logger.error(f"Error inserting events; adding as malformed. Result:\n{result}")
        # Try to record them as an error
        malformed_result = add_malformatted_events(events)
        result.extend(malformed_result)
        result.warnings.append("Original events were malformatted; attempted to correct.")
        # Set success based on whether malformatted logging succeeded
        result.success = malformed_result.success
        return result
    except Exception:
        # Return the original failed result
        logger.error(f"Also failed to log malformatted events.")
        return result

def add_malformatted_events(events: list[dict]) -> LogResult:
    """
    Add a malformatted event to the database with an error message.
    """
    with db_writer_factory.create_writer() as writer:
        fixed_events = []
        if api_config.add_server_timestamps:
            writer.add_server_timestamps(events)

        for event in events:
            if event is None or not isinstance(event, dict):
                # If this is happening, it's too far gone to record
                continue
            try:
                # Try parsing this one individually
                # If it succeeds, we can just use that
                data = MainTableEvent(**event).model_dump(exclude_none=True)
                fixed_events.append(data)
                continue
            except Exception as e:
                data = event

            required_cols = [
                col for col in spec.main_table.columns
                if col.requirement == Requirement.Required
            ]
            # Supply missing columns in case that's the issue
            for col in required_cols:
                if col.name not in data:
                    data[col.name] = "MISSING"
            fixed_events.append(data)

        result = LogResult(success=True)
        for event in fixed_events:
            one_result = writer.add_events([event])
            if one_result.success:
                result.extend(one_result)
            else:
                error = f"Could not fix malformatted event.\nResult: {one_result}"
                one_result = add_error_event(error, str(event), writer=writer)
                result.extend(one_result)
        return result

def add_error_event(error: str, request: str, writer: SQLWriter | None = None) -> LogResult:
    if writer is None:
        with db_writer_factory.create_writer() as writer:
            return _add_error_event(error, request, writer)
    else:
        return _add_error_event(error, request, writer)

def _add_error_event(error: str, request: str, writer: SQLWriter) -> LogResult:
    error_id = writer.generate_event_id()
    data = {
        Cols.EventType: "LoggingError",
        Cols.EventID: writer.generate_event_id(),
        Cols.ToolInstances: "ProvenaServer",
        Cols.LoggingErrorID: error_id,
    }
    writer.add_server_timestamps([data])
    try:
        result = writer.add_events([data])
    except Exception as e:
        error = f"Could not log error event: {error}\nException: {e}"
        logger.error(error)
        result = LogResult(success=False, errors=[error])

    try:
        writer.add_link_table_entry(
            'linkloggingerror',
            {
                Cols.LoggingErrorID: error_id,
                'Error': error,
                'RequestBody': request
            },
            truncate=True
        )
    except Exception as e:
        error = f"Could not log error message in link table: {error}\nException: {e}"
        logger.error(error)
        result.errors.append(error)
        result.success = False

    return result

class CodeStateSection(BaseModel):
    # TODO: We need to document that this CSE isn't equivalent to the
    # one from vscode (often no path).
    CodeStateSection: str
    Code: str

class SubmissionInfo(BaseModel):
    SubjectIDs: List[str] = Field(..., min_items=1)
    CodeState: List[CodeStateSection]

class SubmitEvent(SubmissionInfo):
    AssignmentID: str
    ToolInstances: str
    Score: Optional[float]
    ScoreDetails: Optional[str]
    TermID: Optional[str]
    CourseID: Optional[str]

@router.post("/submit", operation_id="submit", response_model=LogResult)
def log_submit(event: SubmitEvent, writer: SQLWriter = Depends(create_writer)): # type: ignore
    """
    Submit an event to the database.
    """
    base_event = event.model_dump(exclude_none=True)
    base_event["EventType"] = "Submit"
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
            sub_event["ScoreDetails"] = None
            subjectless_events.append(sub_event)

    if len(subjects) == 1:
        for e in subjectless_events:
            e[Cols.SubjectID] = subjects[0]
        events = subjectless_events
    else:
        events = []
        for subject in subjects:
            for sub_event in subjectless_events:
                new_event = sub_event.copy()
                new_event[Cols.SubjectID] = subject
                events.append(new_event)

    logger.info(f"Logging {len(events)} Submit events", events)

    # Add IDs after generating events, since there are multiple
    # possible code files here...
    add_codestate_ids(events)
    return writer.add_events(events)

# TODO: This should be a get, but I'll update later to no break
# things, since it doesn't really matter...
@router.post("/get_event_count", operation_id="getEventCount")
def get_event_count(info: SubmissionInfo, writer: SQLWriter = Depends(create_writer)): # type: ignore
    manager = writer.context.table_manager
    main_table = manager.get_table(CoreTables.MainTable)
    codestate_sections = get_codestate_sections_for_codestates(
        writer.session, main_table, info.SubjectIDs, info.CodeState
    )
    result = get_event_count_for_codestates(writer.session, main_table, info.SubjectIDs, codestate_sections)
    return result

def get_codestate_sections_for_codestates(session: Session, main_table: Table, subject_ids: List[str], codestate_sections: List[CodeStateSection]) -> set[str]:
    def c(col: str) -> Column:
        return main_table.c[col]

    codestate_ids = [generate_code_hash(cs.Code) for cs in codestate_sections]

    # We prefer efficiency over perfect accuracy here, so we
    # just check the hash and not the code itself.
    statement = select(c(Cols.CodeStateSection).distinct()).where(
        c(Cols.EventType) != EventType.Submit,
        c(Cols.SubjectID).in_(subject_ids),
        c(Cols.CodeStateID).in_(codestate_ids)
    )
    rows = session.execute(statement).fetchall()
    found_sections = {row[0] for row in rows if row[0] is not None}

    if (len(found_sections) == 0):
        # If this doesn't work, try matching by section name only
        # and returning all files that end with this one.
        for cs in codestate_sections:
            statement = select(c(Cols.CodeStateSection).distinct()).where(
                c(Cols.SubjectID).in_(subject_ids),
                c(Cols.EventType) != EventType.Submit,
                or_(
                    c(Cols.CodeStateSection) == cs.CodeStateSection,
                    # This ensures that the paths match exactly or it was
                    # a subpath, just just 2 files with the same ending name.
                    # VSCode always uses / for paths
                    c(Cols.CodeStateSection).endswith('/' + cs.CodeStateSection)
                )
            )
            rows = session.execute(statement).fetchall()
            for row in rows:
                found_sections.add(row[0])
                logger.warning(f"Matched codestate section {cs.CodeStateSection} by suffix to {row[0]}\nCould not find codestate by hash: {generate_code_hash(cs.Code)}.")

    return found_sections

def get_event_count_for_codestates(session: Session, main_table: Table, subject_ids: List[str], codestate_sections: List[str], alread_checked_codestate_sections: set[str] = set()) -> int:

    def c(col: str) -> Column:
        return main_table.c[col]

    # TODO: Also confirm that the code being submitted has logs
    statement = select(func.count()).where(
        main_table.c.SubjectID.in_(subject_ids),
        main_table.c.EventType != EventType.Submit,
        main_table.c.CodeStateSection.in_(codestate_sections)
    )
    result = session.execute(statement).scalar()

    alread_checked_codestate_sections.update(codestate_sections)

    # Check for any other names these codestate sections have had before renames
    other_file_names = select(c(Cols.CodeStateSection).distinct()).where(
        c(Cols.SubjectID).in_(subject_ids),
        c(Cols.DestinationCodeStateSection).in_(codestate_sections)
    )
    other_sections = [row[0] for row in session.execute(other_file_names).fetchall()]
    # logger.info("Other sections to check:", other_sections)
    other_sections_to_check = [s for s in other_sections if s not in alread_checked_codestate_sections]

    if len(other_sections_to_check) > 0:
        result += get_event_count_for_codestates(session, main_table, subject_ids, other_sections_to_check, alread_checked_codestate_sections)

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