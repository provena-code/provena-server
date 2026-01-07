
from fastapi import APIRouter
from fastapi.params import Depends

from sqlalchemy import select
from sqlalchemy.sql.expression import func


from progsnap2.database.reader.sql_reader import SQLReader
from progsnap2.spec.enums import CoreTables
from progsnap2.spec.enums import MainTableColumns as Cols
from provena.api.read.common import create_reader

# No access restrictions for now!
# Not really a threat to security, since session IDs are random UUIDs
# and don't reveal anything important to the student.
router = APIRouter(prefix="/read")

@router.get("/sessions/{session_id}/last_synced_order", operation_id="getLastSyncedOrder")
def get_last_synced_log_index(session_id: str, reader: SQLReader = Depends(create_reader)) -> int:
    """
    Get the last synced log index for a given session from the database.
    """
    manager = reader.get_table_manager()
    main_table = manager.get_table(CoreTables.MainTable)
    statement = select(func.max(main_table.c[Cols.Order])).where(
        (main_table.c[Cols.SessionID] == session_id)
    )
    result = reader.get_session().execute(statement).scalar_one()
    return result if result is not None else -1