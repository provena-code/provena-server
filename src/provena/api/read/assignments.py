
from fastapi import APIRouter
from fastapi.params import Depends
from pydantic import BaseModel
from progsnap2.database.reader.sql_reader import SQLReader
from progsnap2.spec.enums import CoreTables, EventType, MainTableColumns as Cols,  EditType
from provena.api.read.common import create_reader, require_api_key
import pandas as pd

from sqlalchemy import and_, case, func, select

router = APIRouter(
    prefix="/read",
    dependencies=[Depends(require_api_key)],
)

@router.get("/assignments", operation_id="getAssignmentIDs")
def get_assignments(reader: SQLReader = Depends(create_reader)):
    manager = reader.get_table_manager()
    # TODO: It would be great to have an Assignments table
    main_table = manager.get_table(CoreTables.MainTable)
    statement = select(main_table.c[Cols.AssignmentID].distinct())
    results = reader.get_session().execute(statement).fetchall()
    ids = [row[0] for row in results if row[0] is not None]
    return ids

class AssignmentSubjectsResponseItem(BaseModel):
    SubjectID: str
    LastSubmissionTime: str
    MaxScore: float

@router.get("/assignments/{assignment_id}/subjects", operation_id="getSubjectStatsForAssignment")
def get_subject_stats_for_assignment(assignment_id: str, reader: SQLReader = Depends(create_reader)) -> list[AssignmentSubjectsResponseItem]:
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    session = reader.get_session()

    submissions = select(
        main_table.c.SubjectID,
        func.max(main_table.c.ServerTimestamp).label("LastSubmissionTime"),
        func.max(main_table.c.Score).label("MaxScore")
    ).where(
        (main_table.c.AssignmentID == assignment_id) &
        (main_table.c.EventType == EventType.Submit) &
        (main_table.c.SubjectID.isnot(None))
    ).group_by(main_table.c.SubjectID)

    results = session.execute(submissions).fetchall()
    return results

    # This old version attempted to get stats for each submission, but it
    # turns out this would require a lot of indexing, and I think it's better
    # to think about this as a chron task that extracts more useful stats more
    # efficiently.

    # mapping_table = get_mapping_table(session, manager)

    # # Find all the Submissions for this AssignmentID
    # # and get who submitted what
    # submitted_files = select(
    #     mapping_table.c.SubjectID,
    #     mapping_table.c.CodeStateSection,
    #     mapping_table.c.LastValidTimestamp,
    # ).where(and_(
    #     main_table.c.AssignmentID == assignment_id,
    #     main_table.c.SubjectID.isnot(None),
    # )).cte("submitted_files")

    # statement = select(
    #     main_table.c.SubjectID,
    #     func.sum(case((main_table.c.EditType == str(EditType.Insert), 1), else_=0)).label("Insertions"),
    #     func.sum(case((main_table.c.EditType == str(EditType.Delete), 1), else_=0)).label("Deletions"),
    #     func.sum(case((main_table.c.EditType == str(EditType.Replace), 1), else_=0)).label("Replacements")
    # ).where(
    #     and_(
    #         main_table.c.EventType == EventType.FileEdit,
    #         main_table.c.ServerTimestamp <= submitted_files.c.LastValidTimestamp
    #     )
    # ).join(
    #     submitted_files,
    #     and_(
    #         main_table.c.SubjectID == submitted_files.c.SubjectID,
    #         main_table.c.CodeStateSection == submitted_files.c.CodeStateSection
    #     )
    # ).group_by(
    #     main_table.c.SubjectID
    # )

    # results = session.execute(statement).fetchall()
    # return [AssignmentSubjectsResponseItem(**dict(row)) for row in results]

@router.get("/assignments/{assignment_id}/{subject_id}/code_state_sections", operation_id="getCodeStateSectionsForAssignmentSubject")
def get_code_state_sections_for_assignment_subject(
    assignment_id: str,
    subject_id: str,
    reader: SQLReader = Depends(create_reader)
):
    manager = reader.get_table_manager()
    session = reader.get_session()
    mapping_table = get_mapping_table(session, manager)
    statement = select(mapping_table.c[Cols.CodeStateSection].distinct()).where(
        (mapping_table.c[Cols.AssignmentID] == assignment_id) &
        (mapping_table.c[Cols.SubjectID] == subject_id)
    )
    results = reader.get_session().execute(statement).fetchall()
    ids = [row[0] for row in results]
    return ids

from provena.api.read.logic.mapping import get_mapping_table, update_mapping_table

@router.post("/update_mapping_table", operation_id="updateMappingTable")
def update_mapping_table_endpoint(reader: SQLReader = Depends(create_reader)):
    manager = reader.get_table_manager()
    get_mapping_table(reader.get_session(), manager)