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
