from fastapi import  APIRouter, Depends
from progsnap2.database.reader.sql_reader import SQLReader
from progsnap2.spec.enums import CoreTables, MainTableColumns as Cols, EventType

from provena.api.read.common import create_reader

from sqlalchemy import select

router = APIRouter(prefix="/read")

@router.get("/{assignment_id}/edits")
def get_all_edits(
    assignment_id: str,
    reader: SQLReader = Depends(create_reader)
):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    return get_edits(
        (main_table.c[Cols.AssignmentID] == assignment_id),
        reader
    )

@router.get("/{subject_id}/{assignment_id}/{codestate_section}/edits")
def get_student_edits(
    subject_id: str, assignment_id: str, codestate_section: str,
    reader: SQLReader = Depends(create_reader)
):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    return get_edits(
        (main_table.c[Cols.SubjectID] == subject_id) &
        (main_table.c[Cols.AssignmentID] == assignment_id) &
        (main_table.c[Cols.CodeStateSection] == codestate_section),
        reader
    )

def get_edits(filter: any, reader: SQLReader):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    cols = [
        Cols.EventID, Cols.ClientTimestamp,
        Cols.SourceLocation,
        "InsertText", "DeleteText",
    ]
    cols = [main_table.c[col] for col in cols]
    statement = select(*cols).where(
        (main_table.c[Cols.EventType] == EventType.FileEdit) &
        filter
    )
    results = reader.get_conn().execute(statement).mappings().all()
    return results