"""Importing this package registers every built-in reader."""

from app.ingestion.readers import csv_reader, json_reader, text_reader, xlsx_reader  # noqa: F401
from app.ingestion.readers.base import (  # noqa: F401
    ReadContext,
    Reader,
    ReaderError,
    ReadResult,
    get_reader,
    registered_formats,
)
