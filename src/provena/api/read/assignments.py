
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
    InsertTextLength: int
    DeleteTextLength: int

@router.get("/assignments/{assignment_id}/subjects", operation_id="getSubjectStatsForAssignment")
def get_subject_stats_for_assignment(assignment_id: str, reader: SQLReader = Depends(create_reader)) -> list[AssignmentSubjectsResponseItem]:
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)

    # Find all the Submissions for this AssignmentID
    # and get who submitted what
    submitted_files = select(
        main_table.c.SubjectID,
        main_table.c.CodeStateSection,
        main_table.c.CodeStateID,
        func.max(main_table.c.ServerTimestamp).label("LastSubmissionTime")
    ).where(and_(
        main_table.c.AssignmentID == assignment_id,
        main_table.c.EventType == EventType.Submit,
        main_table.c.SubjectID.isnot(None),
        main_table.c.CodeStateSection.isnot(None)
    )).group_by(
        main_table.c.SubjectID,
        main_table.c.CodeStateSection
    ).cte("submitted_files")

    # TODO: Need to identify CodeStateSections for a given CodeStateID
    # since the CodeStateSection itself is unreliable here (not a full path!)

    select_cols = [
        Cols.SubjectID,
        Cols.InsertText,
        # TODO: Need a different call if using DeleteText
        Cols.DeleteLength,
    ]
    select_cols = [main_table.c[col] for col in select_cols]

    # Final all edits made by these subjects in these code state sections
    # before the submission
    statement = select(*select_cols).where(
        and_(
            main_table.c.EventType == EventType.FileEdit,
            main_table.c.ServerTimestamp <= submitted_files.c.LastSubmissionTime
        )
    ).join(
        submitted_files,
        and_(
            main_table.c.SubjectID == submitted_files.c.SubjectID,
            main_table.c.CodeStateSection == submitted_files.c.CodeStateSection
        )
    )

    # statement = select(*select_cols).where(
    #     (main_table.c[Cols.AssignmentID] == assignment_id) &
    #     (main_table.c[Cols.EventType] == EventType.FileEdit)
    # )
    edits = pd.read_sql_query(statement, reader.get_session().connection())
    print(edits)
    # Get the sum of inserted and deleted text lengths per subject
    edits[Cols.InsertText + "Length"] = edits[Cols.InsertText].str.len().fillna(0)
    edits["DeleteTextLength"] = edits[Cols.DeleteLength]
    summary = edits.groupby(Cols.SubjectID).agg({
        Cols.InsertText + "Length": "sum",
        "DeleteTextLength": "sum"
    }).reset_index()
    as_dict = summary.to_dict(orient="records")
    return [AssignmentSubjectsResponseItem(**item) for item in as_dict]

@router.get("/assignments/{assignment_id}/{subject_id}/code_state_sections", operation_id="getCodeStateSectionsForAssignmentSubject")
def get_code_state_sections_for_assignment_subject(
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
    results = reader.get_session().execute(statement).fetchall()
    ids = [row[0] for row in results]
    return ids
