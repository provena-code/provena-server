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

@router.get("/{assignment_id}/edits")
def get_all_edits(
    assignment_id: str,
    reader: SQLReader = Depends(create_reader)
):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    result = _get_edits(
        (main_table.c[Cols.AssignmentID] == assignment_id),
        reader
    )
    # convert to a plain list of dicts
    result = [dict(row) for row in result]
    subject_map = {}
    for row in result:
        subject_id = row[Cols.SubjectID]
        if subject_id not in subject_map:
            # TODO: Remove
            if len(subject_map) >= 3:
                continue
            subject_map[subject_id] = []
        subject_map[subject_id].append(row)
    edits = process_edits(subject_map)
    return edits

@router.get("/edits", operation_id="getFileEdits")
def get_student_edits(
    subject_id: Annotated[str, Query(description="SubjectID")],
    codestate_section: Annotated[str, Query(description="CodestateSection")],
    reader: SQLReader = Depends(create_reader)
):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    result = _get_edits(
        (main_table.c[Cols.SubjectID] == subject_id) &
        # (main_table.c[Cols.AssignmentID] == assignment_id) &
        (main_table.c[Cols.CodeStateSection] == codestate_section),
        reader
    )
    # convert to a plain list of dicts
    result = [dict(row) for row in result]
    return result

def _get_edits_query(filter: any, reader: SQLReader):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    # cols = [
    #     Cols.SubjectID, Cols.EventID, Cols.ClientTimestamp,
    #     Cols.SourceLocation,
    #     "InsertText", "DeleteText", "DeleteLength"
    # ]
    # cols = [main_table.c[col] for col in cols]
    # statement = select(*cols).where(
    statement = select(main_table).where(
        # (main_table.c[Cols.EventType] == EventType.FileEdit) &
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