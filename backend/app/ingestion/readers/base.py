"""Reader contract and registry. Add a format by subclassing `Reader` and decorating with `@register`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.config import SourcesConfig
from app.domain.types import RawRow
from app.ingestion.fields import FieldMatcher


class ReaderError(Exception):
    """The file cannot be read in the detected format (corrupt, wrong encoding, no data...)."""


@dataclass(slots=True)
class ReadContext:
    filename: str
    config: SourcesConfig
    matcher: FieldMatcher


@dataclass(slots=True)
class ReadResult:
    format: str
    rows: list[RawRow]
    header: list[str] | None = None
    column_mapping: dict[str, str] = field(default_factory=dict)  # original header -> canonical field
    notes: list[str] = field(default_factory=list)


class Reader(ABC):
    format: str = ""
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def read(self, content: bytes, ctx: ReadContext) -> ReadResult: ...


_REGISTRY: dict[str, Reader] = {}


def register(cls: type[Reader]) -> type[Reader]:
    _REGISTRY[cls.format] = cls()
    return cls


def get_reader(fmt: str) -> Reader:
    try:
        return _REGISTRY[fmt]
    except KeyError as exc:
        raise ReaderError(f"Unsupported format '{fmt}'. Supported: {sorted(_REGISTRY)}") from exc


def registered_formats() -> dict[str, tuple[str, ...]]:
    return {fmt: reader.extensions for fmt, reader in _REGISTRY.items()}


def decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = content.decode(encoding)
            if encoding == "utf-16" and "\x00" in text:
                continue
            return text
        except UnicodeDecodeError:
            continue
    raise ReaderError("Unable to decode file as text")


def tabular_rows(
    grid: list[tuple[str, list[Any]]], ctx: ReadContext, fmt: str
) -> ReadResult:
    """Shared logic for grid-shaped inputs (CSV, XLSX): find the header row, then emit one RawRow per data row.

    `grid` is a list of (locator, cells). Title/banner rows above the header are skipped automatically.
    """
    scan = grid[: ctx.config.parsing.header_scan_rows]
    header_pos = next((i for i, (_, cells) in enumerate(scan) if ctx.matcher.looks_like_header(cells)), None)
    notes: list[str] = []
    if header_pos is None:
        header_pos = next((i for i, (_, cells) in enumerate(grid) if any(_present(c) for c in cells)), None)
        if header_pos is None:
            raise ReaderError("File contains no data")
        notes.append("No recognisable header row found; first non-empty row used as header")
    elif header_pos > 0:
        notes.append(f"Skipped {header_pos} banner row(s) above the header")

    header_locator, header_cells = grid[header_pos]
    headers = [str(c).strip() if _present(c) else f"column_{i + 1}" for i, c in enumerate(header_cells)]
    col_map = ctx.matcher.map_columns(headers)

    rows: list[RawRow] = []
    for locator, cells in grid[header_pos + 1 :]:
        if not any(_present(c) for c in cells):
            continue
        payload = {headers[i]: _jsonable(cells[i]) for i in range(min(len(headers), len(cells)))}
        fields = {canonical: cells[idx] if idx < len(cells) else None for idx, canonical in col_map.items()}
        rows.append(RawRow(locator=locator, payload=payload, fields=fields))
    return ReadResult(
        format=fmt,
        rows=rows,
        header=headers,
        column_mapping={headers[i]: c for i, c in col_map.items()},
        notes=notes + [f"Header detected at {header_locator}"],
    )


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
