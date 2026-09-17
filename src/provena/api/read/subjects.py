from fastapi import APIRouter
from fastapi.params import Depends
from pydantic import BaseModel
from progsnap2.database.reader.sql_reader import SQLReader
from progsnap2.spec.enums import CoreTables, EventType, MainTableColumns as Cols
from provena.api.read.common import create_reader, require_api_key
import pandas as pd

from sqlalchemy import and_, func, select

router = APIRouter(
    prefix="/read",
    dependencies=[Depends(require_api_key)],
)

@router.get("/subjects", operation_id="getSubjectIDs")
def get_subjects(reader: SQLReader = Depends(create_reader)):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    statement = select(main_table.c[Cols.SubjectID].distinct())
    results = reader.get_session().execute(statement).fetchall()
    ids = [row[0] for row in results if row[0] is not None]
    return ids


@router.get("/subjects/{subject_id}/time_range", operation_id="getClientTimestampRangeForSubject")
def get_client_timestamp_range_for_subject(subject_id: str, reader: SQLReader = Depends(create_reader)):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    statement = select(func.min(main_table.c[Cols.ClientTimestamp]), func.max(main_table.c[Cols.ClientTimestamp])).where(main_table.c[Cols.SubjectID] == subject_id)
    result = reader.get_session().execute(statement).fetchone()
    return {
        "MinClientTimestamp": result[0],
        "MaxClientTimestamp": result[1],
    }

@router.get("/subjects/{subject_id}/codestate_sections", operation_id="getCodeStateSectionsForSubject")
def get_codestates_for_subject(subject_id: str, reader: SQLReader = Depends(create_reader)):
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    statement = select(
        main_table.c[Cols.CodeStateSection].distinct()
    ).where(
        main_table.c[Cols.SubjectID] == subject_id,
        # Require at least one save to be considered meaningful
        # This also ignores Submits which use different CodeStateSections
        main_table.c[Cols.EventType] == EventType.FileSave,
        main_table.c[Cols.CodeStateSection].isnot(None),
    )
    results = reader.get_session().execute(statement).fetchall()
    ids = [row[0] for row in results if row[0] is not None]
    return ids