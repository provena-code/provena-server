from enum import Enum
import os
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Type

from fastapi.responses import JSONResponse, PlainTextResponse

from progsnap2.api.config import PS2APIConfig
from progsnap2.api.models import TempCodeStateEntry
from progsnap2.database.config import PS2DataConfig
from progsnap2.database.writer.sql_writer import SQLWriter
from progsnap2.api.events import DataModelGenerator
from progsnap2.database.writer.db_writer import DBWriter, LogResult
from progsnap2.database.writer.db_writer_factory import IOFactory, SQLIOFactory
from progsnap2.spec.spec_definition import PS2Versions, ProgSnap2Spec
from progsnap2.spec.gen.gen_client import generate_ts_methods

__file_dir = os.path.dirname(os.path.abspath(__file__))
__src_dir = os.path.join(__file_dir, "..")

spec = ProgSnap2Spec.from_yaml(os.path.join(__src_dir, "provena/progsnap2-provena.yaml"))

data_model_gen = DataModelGenerator(spec)
MainTableEvent = data_model_gen.MainTableEvent
AnyAdditionalColumns = data_model_gen.AnyAdditionalColumns
SubmitEvent = data_model_gen.main_event_additional_columns.get("Submit")

api_config = PS2APIConfig.from_yaml(os.path.join(__src_dir, "provena/write_config.yaml"), spec)
read_config = PS2DataConfig.from_yaml(os.path.join(__src_dir, "provena/read_config.yaml"), spec)