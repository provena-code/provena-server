
from progsnap2.spec.enums import EventType

from sqlalchemy import Table
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func




def update_mapping_table(session: Session, MainTable: Table, MappingTable: Table):

    last_update = session.query(func.max(MappingTable.LastValidTimestamp)).scalar() or 0

    # 1. Subquery for Latest Submissions
    latest_subs = session.query(
        MainTable.SubjectID,
        MainTable.AssignmentID,
        MainTable.CodeStateID,
        MainTable.CodeStateSection,
        MainTable.ServerTimestamp,
        func.rank().over(
            partition_by=[MainTable.SubjectID, MainTable.AssignmentID],
            order_by=MainTable.ServerTimestamp.desc()
        ).label('rank')
    ).filter(
        MainTable.EventType == EventType.Submit,
        MainTable.ServerTimestamp > last_update
    ).subquery()

    # 2. Final Query with the Path Logic
    query = session.query(
        MainTable.SubjectID,
        MainTable.CodeStateSection,
        latest_subs.c.AssignmentID,
        latest_subs.c.ServerTimestamp
    ).join(
        latest_subs,
        and_(
            MainTable.CodeStateID == latest_subs.c.CodeStateID,
            MainTable.SubjectID == latest_subs.c.SubjectID
        )
    ).filter(
        latest_subs.c.rank == 1,
        MainTable.EventType != EventType.Submit,
        or_(
            MainTable.CodeStateSection == latest_subs.c.CodeStateSection,
            MainTable.CodeStateSection.like(func.concat('%/', latest_subs.c.CodeStateSection))
        )
    ).distinct()

    # Efficiently insert back into mapping table,
    # updating on conflict
