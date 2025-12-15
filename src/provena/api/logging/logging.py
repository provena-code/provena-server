from enum import Enum
import os
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Type

from fastapi.responses import JSONResponse, PlainTextResponse

from progsnap2.api.config import PS2APIConfig
from progsnap2.api.models import TempCodeStateEntry
from progsnap2.database.writer.sql_writer import SQLWriter
from progsnap2.api.events import DataModelGenerator
from progsnap2.database.writer.db_writer import DBWriter, LogResult
from progsnap2.database.writer.db_writer_factory import IOFactory, SQLIOFactory
from progsnap2.spec.spec_definition import PS2Versions, ProgSnap2Spec
from progsnap2.spec.gen.gen_client import generate_ts_methods

from provena.configs import api_config, spec, MainTableEvent, AnyAdditionalColumns

db_writer_factory: SQLIOFactory = IOFactory.create_factory(api_config.database_config, ps2_spec=spec)

# TODO: Don't actually do this automatically every time...
with db_writer_factory.create_writer() as writer:
    # Create the tables in the database
    writer.initialize_database()

# For use in Depends
def create_writer():
    with db_writer_factory.create_writer() as writer:
        yield writer


# TODO: Should probably have a prefix
router = APIRouter()

@router.post("/events", operation_id="addEvents", response_model=LogResult)
def add_events_with_code_states(events: List[MainTableEvent], writer: SQLWriter = Depends(create_writer)): # type: ignore
    """
    Add events and code states to the database at the same time to ensure consistency.

    Note: TempCodeState.code_state_id is a temporary ID that will be remapped when logging
    the events. It is used to map multiple events to the same code state in this request.
    """
    events = [event.model_dump(exclude_none=True) for event in events]
    if api_config.add_server_timestamps:
        writer.add_server_timestamps(events)

    return writer.add_events(events)


# @router.get("/generate_api_helper", operation_id="generateAPIHelper", response_class=PlainTextResponse)
# def generate_api_helper() -> str:
#     return generate_ts_methods(spec)


# I don't think this is needed (or the whole type), but I'll keep for now
# @router.get("/placeholder")
# def get_additional_column_types(additionalColumns: AnyAdditionalColumns): # type: ignore
#     """
#     Placeholder endpoint to get the additional column types.
#     """
#     pass