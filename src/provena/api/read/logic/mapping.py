
import logging

from progsnap2.database.sql_table_manager import SQLTableManager
logger = logging.getLogger(__name__)

from select import select
from progsnap2.spec.enums import EventType
from progsnap2.spec.enums import MainTableColumns as Cols, CoreTables

from sqlalchemy import Column, Table, insert
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from sqlalchemy.dialects.mysql import insert as mysql_insert


def get_mapping_table(session: Session, table_manager: SQLTableManager) -> Table:
    mapping_table =  table_manager.get_table("linkassignmentmap")
    main_table = table_manager.get_table(CoreTables.MainTable)
    update_mapping_table(session, main_table, mapping_table)
    return mapping_table

def update_mapping_table(session: Session, main_table: Table, mapping_table: Table):
    """
    This function updates the mapping table with the CodeStateSections (files) that were submitted
    for each Subject/Assignment pair. Note that a file may be used in multiple Assignments.
    It should be run periodically to keep the mapping table up to date with new submissions and renames.
    """

    logger.info("Updating mapping table with new submissions...")

    last_update = session.query(func.max(mapping_table.c.LastValidTimestamp)).scalar() or "0"

    # First, we get all submissions that have happened since the last update.
    # Submissions include the AssignmentID, and an approximate CodeStateSection (just the filename).
    latest_subs = session.query(
        main_table.c.SubjectID,
        main_table.c.AssignmentID,
        main_table.c.CodeStateID,
        main_table.c.CodeStateSection,
        main_table.c.ServerTimestamp,
        # Get the most recent submission for each SubjectID + AssignmentID
        func.rank().over(
            partition_by=[main_table.c.SubjectID, main_table.c.AssignmentID],
            order_by=main_table.c.ServerTimestamp.desc()
        ).label('rank')
    ).filter(
        main_table.c.EventType == EventType.Submit,
        # Only consider submissions that have occurred since the last update to the mapping table
        main_table.c.ServerTimestamp > last_update
    ).subquery()

    # Now we find the *real* CodeStateSections that correspond to those submitted CodeStateSections
    mapping_query = session.query(
        main_table.c.SubjectID.label(Cols.SubjectID),
        main_table.c.CodeStateSection.label(Cols.CodeStateSection),
        latest_subs.c.AssignmentID.label(Cols.AssignmentID),
        # The submission time becomes the "LastValidTimestamp" for this mapping,
        # which tells us the latest time for which the file in question
        # should be associated with the given AssignmentID.
        # Rdits after this weren't submitted, so they likely aren't associated with the Assignment.
        latest_subs.c.ServerTimestamp.label("LastValidTimestamp")
    ).join(
        latest_subs,
        and_(
            main_table.c.CodeStateID == latest_subs.c.CodeStateID,
            main_table.c.SubjectID == latest_subs.c.SubjectID
        )
    ).filter(
        # Include only the most recent submission for each SubjectID + AssignmentID
        latest_subs.c.rank == 1,

        # Exclude Submissions themselves since they can have unreliable CodeStateSections
        # (not full paths, and can be reused across different files)
        main_table.c.EventType != EventType.Submit,

        # We require either that the CodeStateSections match exactly,
        # or that the MainTable CodeStateSection is a path that ends with the
        # submitted CodeStateSection
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
    # This should probably be done here, so we don't have to redo that work every time

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

