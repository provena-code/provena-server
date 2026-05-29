from typing import Annotated
from fastapi import  APIRouter, Depends, Query
from progsnap2.database.reader.sql_reader import SQLReader
from progsnap2.spec.enums import CoreTables, MainTableColumns as Cols, EventType

from provena.api.read.common import create_reader, require_api_key
from provena.bridge.node_bridge import process_edits

from sqlalchemy import select

router = APIRouter(
    prefix="/read",
    dependencies=[Depends(require_api_key)],
)

@router.get("/edits", operation_id="getFileEdits")
def get_student_edits(
    subject_id: Annotated[str, Query(description="SubjectID")],
    codestate_section: Annotated[str, Query(description="CodeStateSection")],
    last_codestate_id: Annotated[str, Query(description="Last CodeStateID")] = None,
    reader: SQLReader = Depends(create_reader)
):
    end_timestamp = _get_end_client_timestamp(last_codestate_id, reader)

    # Get edit time ranges for each CodeStateSection that's been renamed to this
    ranges = _get_all_edit_ranges(subject_id, codestate_section, end_timestamp, reader)
    # TODO: Remove
    print(ranges)
    # Then get the edits for each range and combine them
    edits = _fetch_edit_ranges(subject_id, ranges, reader)
    # convert to a plain list of dicts
    result = [dict(row) for row in edits]
    return result

def _get_end_client_timestamp(last_code_state_id: str, reader: SQLReader):
    if not last_code_state_id:
        return None

    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)

    # Get the first ClientTimestamp that matches this CodeStateID,
    # For speed we use the first by insertion order, but that should
    # almost always be the first that actually happened on the client
    # and there's little harm to getting this wrong, since either way
    # we end in the submitted state.
    statement = select(main_table.c.ClientTimestamp).where(
        main_table.c.CodeStateID == last_code_state_id
    ).limit(1)

    result = reader.get_session().execute(statement).scalar_one_or_none()
    return result

def _get_all_edit_ranges(subject_id, final_codestate_section: str, max_client_timestamp: str, reader: SQLReader):
    ranges = [{
        Cols.CodeStateSection: final_codestate_section,
        "MaxClientTimestamp": max_client_timestamp,
    }]

    # Find the most recent rename where DestinationCodestateSection == final_codestate_section, and get the SourceCodeStateSection from that rename.
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)

    condition = (main_table.c[Cols.SubjectID] == subject_id) & \
        (main_table.c[Cols.EventType] == EventType.FileRename) & \
        (main_table.c[Cols.DestinationCodeStateSection] == final_codestate_section)
    if max_client_timestamp:
        condition = condition & (main_table.c[Cols.ClientTimestamp] <= max_client_timestamp)

    rename_query = select(
        main_table.c[Cols.CodeStateSection],
        main_table.c[Cols.ClientTimestamp].label("MaxClientTimestamp")
    ).where(condition).order_by(
        main_table.c[Cols.ClientTimestamp].desc()
    ).limit(1)

    rename_result = reader.get_session().execute(rename_query).mappings().first()
    if rename_result is None:
        return ranges

    # If there was a rename, recurse to find earlier ranges
    ranges = _get_all_edit_ranges(
        subject_id,
        rename_result[Cols.CodeStateSection],
        rename_result["MaxClientTimestamp"],
        reader
    ) + ranges

    return ranges


def _fetch_edit_ranges(subject_id: str, ranges: list[dict], reader: SQLReader):
    all_edits = []
    main_table = reader.get_table_manager().get_table(CoreTables.MainTable)
    for i in range(len(ranges)):
        edit_range = ranges[i]
        condition = (main_table.c[Cols.SubjectID] == subject_id) & \
            (main_table.c[Cols.CodeStateSection] == edit_range[Cols.CodeStateSection])
        if edit_range["MaxClientTimestamp"]:
            condition = condition & (main_table.c[Cols.ClientTimestamp] <= edit_range["MaxClientTimestamp"])
        if i > 0:
            prior_range = ranges[i - 1]
            condition = condition & (main_table.c[Cols.ClientTimestamp] >= prior_range["MaxClientTimestamp"])
        edits = _get_edits(condition, reader)
        all_edits += edits
    return all_edits


# TODO: This should also work with renames!
def _get_edits_query(filter: any, reader: SQLReader):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    statement = select(main_table).where(
        # Just use client events for now...
        (main_table.c[Cols.ClientTimestamp] != None) &
        filter
    ).order_by(
        main_table.c[Cols.ClientTimestamp].asc(),
        main_table.c[Cols.Order].asc()
    )
    return statement

def _get_edits(filter: any, reader: SQLReader):
    statement = _get_edits_query(filter, reader)
    results = reader.get_session().execute(statement).mappings().all()
    return results