
from fastapi import APIRouter
from fastapi.params import Depends
from progsnap2.database.reader.sql_reader import SQLReader
from progsnap2.spec.enums import CoreTables, MainTableColumns as Cols
from provena.api.read.common import create_reader

from sqlalchemy import select

router = APIRouter(prefix="/read")

@router.get("/assignments")
def get_assignments(reader: SQLReader = Depends(create_reader)):
    manager = reader.get_table_manager()
    # TODO: It would be great to have an Assignments table
    main_table = manager.get_table(CoreTables.MainTable)
    statement = select(main_table.c[Cols.AssignmentID].distinct())
    results = reader.get_conn().execute(statement).fetchall()
    ids = [row[0] for row in results]
    return ids

@router.get("/assignments/{assignment_id}/subjects")
def get_assignments(assignment_id: str, reader: SQLReader = Depends(create_reader)):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    statement = select(main_table.c[Cols.SubjectID].distinct()).where(
        main_table.c[Cols.AssignmentID] == assignment_id
    )
    results = reader.get_conn().execute(statement).fetchall()
    ids = [row[0] for row in results]
    return ids

@router.get("/assignments/{assignment_id}/{subject_id}/code_state_sections")
def get_assignments(
    assignment_id: str,
    subject_id: str,
    reader: SQLReader = Depends(create_reader)
):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    statement = select(main_table.c[Cols.CodeStateSection].distinct()).where(
        (main_table.c[Cols.AssignmentID] == assignment_id) &
        (main_table.c[Cols.SubjectID] == subject_id)
    )
    results = reader.get_conn().execute(statement).fetchall()
    ids = [row[0] for row in results]
    return ids