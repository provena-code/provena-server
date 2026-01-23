
import logging
logger = logging.getLogger(__name__)

from select import select
from progsnap2.spec.enums import EventType
from progsnap2.spec.enums import MainTableColumns as Cols

from sqlalchemy import Column, Table, insert
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from sqlalchemy.dialects.mysql import insert as mysql_insert


def update_mapping_table(session: Session, main_table: Table, mapping_table: Table):

    last_update = session.query(func.max(mapping_table.c.LastValidTimestamp)).scalar() or "0"

    # 1. Subquery for Latest Submissions
    latest_subs = session.query(
        main_table.c.SubjectID,
        main_table.c.AssignmentID,
        main_table.c.CodeStateID,
        main_table.c.CodeStateSection,
        main_table.c.ServerTimestamp,
        func.rank().over(
            partition_by=[main_table.c.SubjectID, main_table.c.AssignmentID],
            order_by=main_table.c.ServerTimestamp.desc()
        ).label('rank')
    ).filter(
        main_table.c.EventType == EventType.Submit,
        main_table.c.ServerTimestamp > last_update
    ).subquery()

    # 2. Final Query with the Path Logic
    mapping_query = session.query(
        main_table.c.SubjectID.label(Cols.SubjectID),
        main_table.c.CodeStateSection.label(Cols.CodeStateSection),
        latest_subs.c.AssignmentID.label(Cols.AssignmentID),
        latest_subs.c.ServerTimestamp.label("LastValidTimestamp")
    ).join(
        latest_subs,
        and_(
            main_table.c.CodeStateID == latest_subs.c.CodeStateID,
            main_table.c.SubjectID == latest_subs.c.SubjectID
        )
    ).filter(
        latest_subs.c.rank == 1,
        # TODO: Undo: just for testing
        # main_table.c.EventType != EventType.Submit,
        or_(
            main_table.c.CodeStateSection == latest_subs.c.CodeStateSection,
            main_table.c.CodeStateSection.like(func.concat('%/', latest_subs.c.CodeStateSection))
        )
    ).distinct()

    results = mapping_query.all()
    logger.info(f"Adding {len(results)} rows to mapping table.")

    if not results:
        return  # Nothing to update

    # TODO: Add renames!

    stmnt = mysql_insert(mapping_table).values([
        {
            Cols.SubjectID: row.SubjectID,
            Cols.AssignmentID: row.AssignmentID,
            Cols.CodeStateSection: row.CodeStateSection,
            "LastValidTimestamp": row.LastValidTimestamp
        }
        for row in results
    ])

    # Efficiently insert back into mapping table, updating LastValidTimestamp on conflict
    stmnt = stmnt.on_duplicate_key_update(
        LastValidTimestamp=stmnt.inserted.LastValidTimestamp
    )

    session.execute(stmnt)
    session.commit()


# TODO: This is incomplete, and I'm not sure of a good way to
# do this in a batch and still be recursive...
def update_mapping_table_for_renames(session: Session, main_table: Table, mapping_table: Table, last_update: str):
    def c(col: str) -> Column:
        return main_table.c[col]

    # TODO: This means we need an index on LastValidTimestamp in the mapping table
    # First get all CodeStateSections that have been mapped since the last update
    recent_mappings = session.query(
        mapping_table.c.SubjectID,
        mapping_table.c.AssignmentID,
        mapping_table.c.CodeStateSection
    ).filter(
        mapping_table.c.LastValidTimestamp > last_update
    ).subquery()

    # Merge the recent_mappings with any rename events in the MainTable
    # Where the DestinationCodeStateSection matches a recently mapped CodeStateSection
    renames = session.query(
        c(Cols.SubjectID),
        c(Cols.CodeStateSection),
        recent_mappings.c.AssignmentID,
        recent_mappings.c.LastValidTimestamp
    ).join(
        recent_mappings,
        and_(
            c(Cols.SubjectID) == recent_mappings.c.SubjectID,
            c(Cols.DestinationCodeStateSection) == recent_mappings.c.CodeStateSection
        )
    ).filter(
        c(Cols.EventType) == EventType.Rename
    ).subquery()

